from django.contrib import admin

from .models import (
    InsuranceCompany, InsuranceMember,
    InsuranceClaim, InsuranceClaimLine, InsuranceClaimSettlement,
)

admin.site.register(InsuranceCompany)
admin.site.register(InsuranceMember)
admin.site.register(InsuranceClaim)
admin.site.register(InsuranceClaimLine)
admin.site.register(InsuranceClaimSettlement)
