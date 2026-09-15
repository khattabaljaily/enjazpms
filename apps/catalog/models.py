"""
Master drug catalog — shared reference data, NOT tenant-scoped.

This is deliberately separate from apps.items.models.Item (which stays
fully per-tenant): the catalog holds drug-inherent facts (generic name,
dosage form, strength, manufacturer, classification, prescription flag)
that are the same for every tenant, never tenant-specific pricing/stock.
"""
from django.conf import settings
from django.db import models

from .matching import normalize_text, normalize_strength


class MasterCategory(models.Model):
    """Global classification tree, independent of each tenant's own Category."""
    name = models.CharField('الاسم', max_length=200, unique=True)
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='children', verbose_name='التصنيف الرئيسي'
    )
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'catalog_master_categories'
        verbose_name = 'تصنيف رئيسي'
        verbose_name_plural = 'التصنيفات الرئيسية'
        ordering = ['name']

    def __str__(self):
        return self.name


class MasterDrug(models.Model):
    """
    The substitutable unit: one row per (generic_name, dosage_form, strength)
    combination — this is the grouping alternatives get computed over.
    """
    STATUS_CHOICES = (
        ('active', 'نشط'),
        ('merged', 'مدموج'),
        ('deprecated', 'متوقف'),
    )

    generic_name = models.CharField('الاسم العلمي / المادة الفعالة', max_length=300)
    generic_name_normalized = models.CharField(max_length=300, db_index=True, editable=False, blank=True)
    dosage_form = models.CharField('الشكل الصيدلاني', max_length=100, blank=True)
    dosage_form_normalized = models.CharField(max_length=100, db_index=True, editable=False, blank=True)
    strength = models.CharField('التركيز', max_length=300, blank=True)
    strength_normalized = models.CharField(max_length=100, db_index=True, editable=False, blank=True)

    category = models.ForeignKey(
        MasterCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='drugs', verbose_name='التصنيف'
    )
    default_unit_name = models.CharField('الوحدة المقترحة', max_length=50, blank=True)
    default_dosage_conversion = models.JSONField(
        'تحويلات الوحدات المقترحة', default=list, blank=True,
        help_text='مثال: [{"name": "شريط", "factor": 10}, {"name": "علبة", "factor": 100}]'
    )

    # ------ حقول موازية لـ apps.items.models.Item — تُستخدم كقيم افتراضية
    # مقترحة تُعبّأ تلقائياً عند إنشاء صنف من الكتالوج، ويبقى المشترك حراً
    # يعدّلها. الهدف: لا حقل في Item غير متاح لا من الكتالوج ولا كإدخال
    # للمشترك. ------
    item_type = models.CharField('نوع الصنف', max_length=20, default='product')
    description = models.TextField('الوصف', blank=True)
    image = models.ImageField('صورة تعريفية', upload_to='catalog/master_drugs/', blank=True, null=True)
    track_expiry = models.BooleanField('تتبع تاريخ الانتهاء (مقترح)', default=True)
    track_batch = models.BooleanField('تتبع رقم الدفعة (مقترح)', default=True)
    track_serial = models.BooleanField('تتبع الرقم التسلسلي (مقترح)', default=False)

    requires_prescription = models.BooleanField('يُصرف بوصفة طبية', default=False)
    is_controlled_substance = models.BooleanField('خاضع للرقابة / مخدرات', default=False)
    is_insurance_excluded = models.BooleanField('غير مغطى بالتأمين', default=False)

    status = models.CharField('الحالة', max_length=20, choices=STATUS_CHOICES, default='active')
    merged_into = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='merged_from', verbose_name='مدموج في'
    )
    notes = models.TextField('ملاحظات', blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='master_drugs_created'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='master_drugs_updated'
    )

    class Meta:
        db_table = 'catalog_master_drugs'
        verbose_name = 'دواء (كتالوج رئيسي)'
        verbose_name_plural = 'الكتالوج الرئيسي للأدوية'
        ordering = ['generic_name']
        indexes = [
            models.Index(fields=['generic_name_normalized', 'dosage_form_normalized', 'strength_normalized']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        parts = [self.generic_name]
        if self.strength:
            parts.append(self.strength)
        if self.dosage_form:
            parts.append(self.dosage_form)
        return ' - '.join(parts)

    def save(self, *args, **kwargs):
        self.generic_name_normalized = normalize_text(self.generic_name)
        self.dosage_form_normalized = normalize_text(self.dosage_form)
        self.strength_normalized = normalize_strength(self.strength)
        super().save(*args, **kwargs)

    def resolve(self):
        """Follows the merged_into chain to the surviving MasterDrug."""
        drug = self
        seen = {drug.pk}
        while drug.status == 'merged' and drug.merged_into_id and drug.merged_into_id not in seen:
            drug = drug.merged_into
            seen.add(drug.pk)
        return drug


class MasterDrugAlias(models.Model):
    """A trade name / manufacturer variant of a MasterDrug."""
    master_drug = models.ForeignKey(MasterDrug, on_delete=models.CASCADE, related_name='aliases')
    trade_name = models.CharField('الاسم التجاري', max_length=300)
    trade_name_normalized = models.CharField(max_length=300, db_index=True, editable=False, blank=True)
    manufacturer = models.CharField('الشركة المصنعة', max_length=200, blank=True)
    country_of_origin = models.CharField('بلد المنشأ', max_length=100, blank=True)
    sudan_agent = models.CharField('الوكيل أو الموزع في السودان', max_length=200, blank=True)
    pack_size = models.CharField('حجم العبوة', max_length=300, blank=True)
    barcode = models.CharField('الباركود', max_length=100, blank=True, db_index=True)
    is_primary = models.BooleanField('الاسم الأساسي', default=False)
    source_batch = models.ForeignKey(
        'CatalogImportBatch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='aliases_created'
    )
    notes = models.TextField('ملاحظات', blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='master_drug_aliases_created'
    )

    class Meta:
        db_table = 'catalog_master_drug_aliases'
        verbose_name = 'اسم تجاري'
        verbose_name_plural = 'الأسماء التجارية'
        ordering = ['trade_name']
        unique_together = [('trade_name_normalized', 'manufacturer', 'master_drug')]
        indexes = [
            models.Index(fields=['master_drug']),
        ]

    def __str__(self):
        return f'{self.trade_name} ({self.manufacturer})' if self.manufacturer else self.trade_name

    def save(self, *args, **kwargs):
        self.trade_name_normalized = normalize_text(self.trade_name)
        super().save(*args, **kwargs)
        if self.is_primary:
            MasterDrugAlias.objects.filter(
                master_drug_id=self.master_drug_id
            ).exclude(pk=self.pk).update(is_primary=False)


class CatalogImportBatch(models.Model):
    """One upload session of a heterogeneous supplier price list, staged for review."""
    STATUS_CHOICES = (
        ('pending', 'قيد الانتظار'),
        ('extracting', 'جاري الاستخراج'),
        ('reviewing', 'قيد المراجعة'),
        ('committing', 'جاري الاعتماد'),
        ('committed', 'مكتمل'),
        ('cancelled', 'ملغي'),
    )

    original_filename = models.CharField('اسم الملف', max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catalog_import_batches'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    source_label = models.CharField('مصدر القائمة', max_length=200, blank=True)
    status = models.CharField('الحالة', max_length=20, choices=STATUS_CHOICES, default='pending')
    header_mapping = models.JSONField(default=dict, blank=True)
    total_rows = models.IntegerField(default=0)
    processed_rows = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = 'catalog_import_batches'
        verbose_name = 'دفعة استيراد للكتالوج'
        verbose_name_plural = 'دفعات استيراد الكتالوج'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.original_filename} ({self.get_status_display()})'


class CatalogImportRow(models.Model):
    """One staged row from a CatalogImportBatch's source spreadsheet."""
    MATCH_STATUS_CHOICES = (
        ('pending', 'بانتظار المعالجة'),
        ('new', 'دواء جديد'),
        ('duplicate_exact', 'مطابق تماماً'),
        ('duplicate_fuzzy', 'يحتاج مراجعة'),
        ('needs_review', 'يحتاج مراجعة يدوية'),
        ('rejected', 'مرفوض'),
        ('committed', 'تم الاعتماد'),
    )

    batch = models.ForeignKey(CatalogImportBatch, on_delete=models.CASCADE, related_name='rows')
    row_number = models.IntegerField()
    raw_data = models.JSONField(default=dict)
    extracted_data = models.JSONField(default=dict, blank=True)
    match_status = models.CharField(max_length=20, choices=MATCH_STATUS_CHOICES, default='pending')
    matched_master_drug = models.ForeignKey(
        MasterDrug, on_delete=models.SET_NULL, null=True, blank=True, related_name='import_rows'
    )
    confidence_score = models.FloatField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catalog_rows_reviewed'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'catalog_import_rows'
        verbose_name = 'صف استيراد كتالوج'
        verbose_name_plural = 'صفوف استيراد الكتالوج'
        ordering = ['batch', 'row_number']
        indexes = [
            models.Index(fields=['batch', 'match_status']),
        ]

    def __str__(self):
        return f'batch #{self.batch_id} row {self.row_number}'
