"""
Computed drug alternatives — augments (does not replace) the manual
`Item.alternatives` M2M. Substitutability here is deliberately EXACT-match
only (never fuzzy): suggesting a "similar but different" drug as an
interchangeable substitute at checkout is a safety-relevant decision, so
this only fires when items share the same active ingredient/dosage/strength
signal, either via a shared MasterDrug link or matching raw fields.
"""
from .models import Item


def get_computed_alternatives(item: Item, limit: int = 10):
    """Live-computed substitutable items — no dependency on the manual M2M."""
    base = Item.objects.for_tenant(item.tenant).filter(
        is_active=True, is_sellable=True
    ).exclude(pk=item.pk)

    if item.master_drug_id:
        return base.filter(master_drug_id=item.master_drug_id)[:limit]

    if item.generic_name:
        return base.filter(
            generic_name__iexact=item.generic_name.strip(),
            dosage_form__iexact=(item.dosage_form or '').strip(),
            strength__iexact=(item.strength or '').strip(),
        )[:limit]

    return base.none()


def get_all_alternatives(item: Item, limit: int = 10) -> list:
    """Manual picks first (curated, can be overridden by the pharmacist), then computed."""
    manual = list(item.alternatives.filter(tenant=item.tenant, is_active=True, is_sellable=True))
    manual_ids = {a.id for a in manual}

    computed = [
        alt for alt in get_computed_alternatives(item, limit=limit)
        if alt.id not in manual_ids
    ]

    return (manual + computed)[:limit]
