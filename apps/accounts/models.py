"""
Accounts Models - نماذج الحسابات والمستخدمين
"""
import json
from pathlib import Path

from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from apps.core.models import Tenant, TenantQuerySet
from .permissions import get_permission_keys, load_permission_schema


class UserQuerySet(TenantQuerySet):
    """QuerySet للمستخدمين مع فلترة بالـ Tenant"""
    pass


class UserManager(BaseUserManager):
    """Manager للمستخدمين"""
    
    def get_queryset(self):
        return UserQuerySet(self.model, using=self._db)
    
    def for_tenant(self, tenant):
        return self.get_queryset().for_tenant(tenant)
    
    def create_user(self, username, email=None, password=None, **extra_fields):
        """إنشاء مستخدم عادي"""
        if not username:
            raise ValueError('يجب توفير اسم مستخدم')
        
        email = self.normalize_email(email) if email else ''
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        """إنشاء مستخدم إداري"""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom User Model
    يمتد من AbstractUser ويضيف حقول إضافية
    """
    
    # Tenant Relationship
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='users',
        null=True,  # NULL للـ Superuser الذي لا يتبع tenant
        blank=True
    )
    
    # Profile
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    avatar = models.ImageField('الصورة الشخصية', upload_to='users/avatars/', blank=True, null=True)
    is_tenant_admin    = models.BooleanField('مدير النشاط', default=False)

    # Platform staff (admin assistants — tenant=None, not full superuser)
    is_platform_staff   = models.BooleanField('مساعد منصة', default=False)
    platform_permissions = models.JSONField('صلاحيات المنصة', default=list, blank=True)
    
    # Additional Info
    date_joined = models.DateTimeField('تاريخ الانضمام', auto_now_add=True)
    last_login = models.DateTimeField('آخر تسجيل دخول', null=True, blank=True)
    is_active = models.BooleanField('نشط', default=True)
    
    # Use custom manager
    objects = UserManager()
    
    class Meta:
        db_table = 'users'
        verbose_name = 'مستخدم'
        verbose_name_plural = 'المستخدمون'
        ordering = ['-date_joined']
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['username']),
            models.Index(fields=['email']),
        ]
    
    def __str__(self):
        return f"{self.get_full_name() or self.username}"
    
    def get_full_name(self):
        """الاسم الكامل"""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.last_name or self.username
    
    def can_access_tenant(self, tenant):
        """هل يمكن للمستخدم الوصول لهذا الـ Tenant"""
        if self.is_superuser:
            return True
        return self.tenant == tenant

    def has_platform_perm(self, perm):
        """Superusers have all platform perms; platform staff only what's granted."""
        if self.is_superuser:
            return True
        if self.is_platform_staff:
            return perm in (self.platform_permissions or [])
        return False

    def get_permission_keys(self):
        if self.is_superuser or self.is_tenant_admin:
            return set(get_permission_keys())
        keys = set()
        for group in self.permission_groups.filter(is_active=True):
            keys.update(group.get_permission_keys())
        return keys

    def has_perm_key(self, permission_key):
        if not permission_key:
            return False
        if self.is_superuser or self.is_tenant_admin:
            return True
        for group in self.permission_groups.filter(is_active=True):
            if group.has_permission(permission_key):
                return True
        return False


class PermissionGroup(models.Model):
    """
    مجموعات الصلاحيات - لكل tenant صلاحياته الخاصة
    """
    
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='permission_groups'
    )
    
    name = models.CharField('اسم المجموعة', max_length=100)
    description = models.TextField('الوصف', blank=True)
    
    # Permissions (JSON Format for flexibility)
    permissions = models.JSONField('الصلاحيات', default=dict)
    
    users = models.ManyToManyField(
        User,
        verbose_name='المستخدمون',
        related_name='permission_groups',
        blank=True
    )
    
    is_active = models.BooleanField('نشط', default=True)
    
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)
    
    class Meta:
        db_table = 'permission_groups'
        verbose_name = 'مجموعة صلاحيات'
        verbose_name_plural = 'مجموعات الصلاحيات'
        unique_together = ['tenant', 'name']
        ordering = ['name']
    
    def __str__(self):
        return f"{self.tenant.name} - {self.name}"

    def get_permission_keys(self):
        if not self.permissions:
            return []
        return [key for key, value in self.permissions.items() if value]

    def set_permission_keys(self, permission_keys):
        all_keys = set(get_permission_keys())
        self.permissions = {key: True for key in permission_keys if key in all_keys}

    def has_permission(self, permission_key):
        """التحقق من وجود صلاحية معينة"""
        if not permission_key or not self.permissions:
            return False
        return bool(self.permissions.get(permission_key, False))

    @classmethod
    def create_owner_group(cls, tenant, name='مدير النشاط'):
        default_keys = get_permission_keys()
        group = cls.objects.create(
            tenant=tenant,
            name=name,
            permissions={ key: True for key in default_keys },
            is_active=True,
        )
        return group

    @classmethod
    def permission_schema(cls):
        return load_permission_schema()


class UserActivity(models.Model):
    ACTION_CREATE  = 'create'
    ACTION_UPDATE  = 'update'
    ACTION_DELETE  = 'delete'
    ACTION_CONFIRM = 'confirm'
    ACTION_CANCEL  = 'cancel'
    ACTION_LOGIN   = 'login'
    ACTION_OTHER   = 'other'

    ACTION_CHOICES = [
        (ACTION_CREATE,  'إنشاء'),
        (ACTION_UPDATE,  'تعديل'),
        (ACTION_DELETE,  'حذف'),
        (ACTION_CONFIRM, 'تأكيد'),
        (ACTION_CANCEL,  'إلغاء'),
        (ACTION_LOGIN,   'تسجيل دخول'),
        (ACTION_OTHER,   'أخرى'),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='user_activities',
        db_index=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم',
        related_name='activities',
    )
    title       = models.CharField('العنوان', max_length=200)
    action_type = models.CharField('نوع الإجراء', max_length=50, choices=ACTION_CHOICES, default=ACTION_OTHER)
    details     = models.TextField('التفاصيل', blank=True)
    ip_address  = models.GenericIPAddressField('عنوان IP', null=True, blank=True)
    created_at  = models.DateTimeField('وقت النشاط', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'user_activities'
        verbose_name = 'نشاط مستخدم'
        verbose_name_plural = 'سجل الأنشطة'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', '-created_at']),
            models.Index(fields=['tenant', 'user', '-created_at']),
        ]

    def __str__(self):
        username = self.user.username if self.user else 'مجهول'
        return f"{username}: {self.title}"

