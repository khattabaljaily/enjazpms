from django.contrib import admin

from .models import MasterCategory, MasterDrug, MasterDrugAlias, CatalogImportBatch, CatalogImportRow


class MasterDrugAliasInline(admin.TabularInline):
    model = MasterDrugAlias
    extra = 0
    fields = ('trade_name', 'manufacturer', 'barcode', 'is_primary')


@admin.register(MasterCategory)
class MasterCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'is_active')
    search_fields = ('name',)


@admin.register(MasterDrug)
class MasterDrugAdmin(admin.ModelAdmin):
    list_display = ('generic_name', 'strength', 'dosage_form', 'category', 'status', 'requires_prescription')
    list_filter = ('status', 'requires_prescription', 'is_controlled_substance', 'category')
    search_fields = ('generic_name', 'generic_name_normalized', 'strength', 'dosage_form')
    inlines = [MasterDrugAliasInline]
    readonly_fields = ('generic_name_normalized', 'dosage_form_normalized', 'strength_normalized')


@admin.register(MasterDrugAlias)
class MasterDrugAliasAdmin(admin.ModelAdmin):
    list_display = ('trade_name', 'manufacturer', 'master_drug', 'barcode', 'is_primary')
    search_fields = ('trade_name', 'trade_name_normalized', 'manufacturer', 'barcode')
    list_filter = ('is_primary',)


class CatalogImportRowInline(admin.TabularInline):
    model = CatalogImportRow
    extra = 0
    fields = ('row_number', 'match_status', 'matched_master_drug', 'confidence_score')
    readonly_fields = ('row_number', 'raw_data', 'extracted_data')
    can_delete = False
    show_change_link = True


@admin.register(CatalogImportBatch)
class CatalogImportBatchAdmin(admin.ModelAdmin):
    list_display = ('original_filename', 'source_label', 'status', 'total_rows', 'processed_rows', 'uploaded_at')
    list_filter = ('status',)
    inlines = [CatalogImportRowInline]


@admin.register(CatalogImportRow)
class CatalogImportRowAdmin(admin.ModelAdmin):
    list_display = ('batch', 'row_number', 'match_status', 'matched_master_drug', 'confidence_score')
    list_filter = ('match_status',)
    readonly_fields = ('raw_data', 'extracted_data')
