"""
Accounts Models - نماذج الحسابات والمستخدمين
"""
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from apps.core.models import Tenant, TenantQuerySet


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
    
    USER_ROLES = (
        ('owner', 'مالك النشاط'),
        ('admin', 'مدير'),
        ('cashier', 'كاشير'),
        ('stock_keeper', 'أمين مخزن'),
        ('accountant', 'محاسب'),
        ('viewer', 'مشاهد فقط'),
    )
    
    # Tenant Relationship
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='العميل',
        related_name='users',
        null=True,  # NULL للـ Superuser الذي لا يتبع tenant
        blank=True
    )
    
    # Profile
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    avatar = models.ImageField('الصورة الشخصية', upload_to='users/avatars/', blank=True, null=True)
    
    # Role & Permissions
    role = models.CharField('الدور الوظيفي', max_length=20, choices=USER_ROLES, default='cashier')
    is_tenant_admin = models.BooleanField('مدير النشاط', default=False)
    
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


class PermissionGroup(models.Model):
    """
    مجموعات الصلاحيات - لكل tenant صلاحياته الخاصة
    """
    
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='العميل',
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
    
    def has_permission(self, permission_key):
        """التحقق من وجود صلاحية معينة"""
        return self.permissions.get(permission_key, False)
