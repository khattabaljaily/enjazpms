from django.contrib import admin
from .models import Agent, AgentLedger


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'phone', 'city', 'commission_type', 'is_active', 'created_at']
    list_filter = ['is_active', 'commission_type', 'tenant']
    search_fields = ['name', 'phone', 'code']
    fieldsets = [
        ('بيانات المندوب', {'fields': ['tenant', 'code', 'name', 'phone', 'email', 'city', 'address', 'notes']}),
        ('العمولة', {'fields': ['commission_type', 'commission_rate', 'opening_balance']}),
        ('الحالة', {'fields': ['is_active']}),
    ]


@admin.register(AgentLedger)
class AgentLedgerAdmin(admin.ModelAdmin):
    list_display = ['agent', 'entry_type', 'amount', 'running_balance', 'entry_date']
    list_filter = ['entry_type', 'tenant']
    search_fields = ['agent__name', 'notes']
