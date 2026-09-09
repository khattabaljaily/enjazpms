from django.contrib import admin

from .models import (
    InsuranceCompany, CustomerInsurancePolicy,
    InsuranceClaim, InsuranceClaimLine, InsuranceClaimSettlement,
)

admin.site.register(InsuranceCompany)
admin.site.register(CustomerInsurancePolicy)
admin.site.register(InsuranceClaim)
admin.site.register(InsuranceClaimLine)
admin.site.register(InsuranceClaimSettlement)
