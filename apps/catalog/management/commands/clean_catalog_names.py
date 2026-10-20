"""
Clean-up of PDF-extraction artefacts in the master catalog:

0. Rows whose scientific and trade names sit in each other's field are swapped
   back (MasterDrug.generic_name <-> primary alias trade_name). A row counts as
   reversed when its "trade" text is paired with more distinct names across
   the catalog than its "generic" text (a scientific name has many brands);
   anything unclear is left alone.
1. Stray lowercase letter in front of trade names ("aAzox 250mg" -> "Azox 250mg").
2. Strength merged into the trade name, no parentheses ("Azox" + "250 mg" ->
   "Azox 250 mg"), so products sharing a brand name stay distinguishable on
   the sales screen. MasterDrug.strength itself is kept.
3. Duplicate company names that differ only by trailing period / case /
   spacing / Ltd-vs-Limited / truncation, merged into one canonical spelling
   (applies to manufacturer and sudan_agent).

Dry-run by default. --apply writes a JSON backup of every changed value;
--restore <file> puts the old values back. Rows whose change would collide
with another alias (unique trade name + manufacturer per drug) are left
untouched and listed for manual review.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.catalog.matching import (
    NamePairIndex, build_manufacturer_canon, clean_product_name, is_clean_strength, is_reversed_pair, name_match_key,
    merge_strength_into_name, normalize_text, strip_trailing_strength,
)
from apps.catalog.models import MasterDrug, MasterDrugAlias

COMPANY_FIELDS = ('manufacturer', 'sudan_agent')
MAX_CYCLES = 4      # later cycles see the evidence cleaned by earlier ones; stops early once stable
ALIAS_BACKUP_FIELDS = (
    'id', 'master_drug_id', 'trade_name', 'trade_name_normalized', 'manufacturer', 'country_of_origin',
    'sudan_agent', 'pack_size', 'barcode', 'is_primary', 'source_batch_id', 'notes', 'created_by_id',
)


class Command(BaseCommand):
    help = 'Fix reversed names, clean PDF artefacts, merge strength into trade names, unify company names.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--backup-dir', default='backups')
        parser.add_argument('--restore', metavar='FILE')
        parser.add_argument('--merge-duplicates', action='store_true',
                            help='Also merge MasterDrug rows with the same generic/form/strength.')
        parser.add_argument('--ai-unclear', action='store_true',
                            help='Ask the AI (DeepSeek, as the catalog import does) about rows with no statistical evidence; only a clear "swap" answer is applied.')
        parser.add_argument('--swap-ai-only', action='store_true',
                            help='For already-cleaned data: skip the statistical reversal (its evidence is weaker after merging) and swap only what the AI confirms. Implies --ai-unclear.')
        parser.add_argument('--disambiguate-forms', action='store_true',
                            help='Append the dosage form to trade names shared by products of different forms.')
        parser.add_argument('--no-swap', action='store_true', help='Skip the reversed-names step.')
        parser.add_argument('--show', type=int, default=15, help='How many examples to print per kind.')

    def handle(self, *args, **opts):
        if opts['restore']:
            return self._restore(opts['restore'])

        apply, show = opts['apply'], opts['show']
        backup = {'drugs': {}, 'aliases': {}, 'moved': {}, 'merged': [], 'deleted': []}
        with transaction.atomic():
            # Everything is written inside one transaction; a dry-run rolls it
            # back, so later steps see (and report) the post-swap names.
            for cycle in range(1, MAX_CYCLES + 1):
                self.stdout.write(self.style.SUCCESS(f'===== دورة {cycle} ====='))
                touched = 0
                # Reversal is decided once, on the untouched data: later cycles have weaker
                # evidence (merged rows, completed names) and would flip correct rows back.
                if cycle == 1 and not opts['no_swap']:
                    touched += self._swap_reversed(
                        backup, show, ai=opts['ai_unclear'] or opts['swap_ai_only'], ai_only=opts['swap_ai_only'])
                touched += self._normalize_generics(backup, show)
                touched += self._clean_aliases(backup, show, with_strength=False)
                if opts['merge_duplicates']:
                    touched += self._merge_duplicates(backup, show)
                # strength goes in last, so aliases moved between drugs by the merge all
                # get the surviving drug's strength text exactly once
                touched += self._clean_aliases(backup, show, with_strength=True)
                if not touched:
                    break      # stable: another cycle would change nothing
            if opts['disambiguate_forms']:
                self._disambiguate_forms(backup, show)
            if not apply:
                transaction.set_rollback(True)

        if not apply:
            self.stdout.write(self.style.WARNING('Dry-run — nothing written. Re-run with --apply.'))
            return
        out = Path(opts['backup_dir'])
        out.mkdir(parents=True, exist_ok=True)
        path = out / f'catalog_clean_backup_{timezone.now():%Y%m%d_%H%M%S}.json'
        path.write_text(json.dumps(backup, ensure_ascii=False), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'Applied. Backup: {path} ({len(backup["aliases"])} aliases, {len(backup["drugs"])} drugs)'))

    # ---- step 0: reversed scientific / trade names -------------------------
    def _swap_reversed(self, backup, show, ai=False, ai_only=False):
        drugs = list(MasterDrug.objects.all())
        by_pk = {d.pk: d for d in drugs}
        all_aliases = list(MasterDrugAlias.objects.order_by('-is_primary', 'pk'))
        index = NamePairIndex((by_pk[a.master_drug_id].generic_name, a.trade_name)
                              for a in all_aliases if a.master_drug_id in by_pk)
        primary = {}
        for a in all_aliases:
            primary.setdefault(a.master_drug_id, a)

        swaps, unclear_rows = [], []
        for d in drugs:
            a = primary.get(d.pk)
            if not a or not d.generic_name.strip():
                continue
            if is_reversed_pair(d.generic_name, a.trade_name, index):
                swaps.append((d, a))
            elif index.degree(d.generic_name) == index.degree(a.trade_name):
                unclear_rows.append((d, a))

        if ai_only:
            swaps = []
        ai_swaps = self._ask_ai(unclear_rows, show) if ai and unclear_rows else []
        swaps += ai_swaps
        unclear = len(unclear_rows) - len(ai_swaps)

        for d, a in swaps:
            backup['drugs'].setdefault(d.pk, d.generic_name)
            backup['aliases'].setdefault(a.pk, {})['trade_name'] = a.trade_name
            new_generic = strip_trailing_strength(a.trade_name)
            MasterDrug.objects.filter(pk=d.pk).update(
                generic_name=new_generic, generic_name_normalized=normalize_text(new_generic))
            MasterDrugAlias.objects.filter(pk=a.pk).update(
                trade_name=d.generic_name, trade_name_normalized=normalize_text(d.generic_name))

        self.stdout.write(self.style.MIGRATE_HEADING(f'أسماء معكوسة: {len(swaps)} من {len(drugs)} دواء'))
        for d, a in swaps[:show]:
            self.stdout.write(f'   علمي {d.generic_name!r} / تجاري {a.trade_name!r}  ->  '
                              f'علمي {strip_trailing_strength(a.trade_name)!r} / تجاري {d.generic_name!r}')
        self.stdout.write(f'unclear (no evidence which side is scientific, left untouched): {unclear}')
        return len(swaps)

    # ---- step 0b: truncated / strength-carrying scientific names ------------
    def _normalize_generics(self, backup, show):
        """A 19-20 char generic cut by the PDF reader is completed from the catalog's
        own longer spelling, and a trailing strength ("Acetazolamide 250") is dropped
        when the clean name exists elsewhere in the catalog — never guessed."""
        import bisect
        from collections import Counter
        drugs = list(MasterDrug.objects.all())
        display = {}
        for name, _ in Counter(d.generic_name.strip() for d in drugs if d.generic_name.strip()).most_common():
            if strip_trailing_strength(name) == name:
                display.setdefault(name_match_key(name), name)
        keys = sorted(display)

        changes = []
        for d in drugs:
            g = d.generic_name.strip()
            if not g:
                continue
            new = g
            stripped = strip_trailing_strength(g)
            if stripped != g and name_match_key(stripped) in display:
                new = display[name_match_key(stripped)]
            elif len(g) >= 15 and g[-1:].isalpha():
                k = name_match_key(g)
                i = bisect.bisect_left(keys, k)
                longer = []
                while i < len(keys) and keys[i].startswith(k):
                    if len(keys[i]) > len(k) and keys[i][len(k)] != ' ':    # cut in the middle of a word
                        longer.append(keys[i])
                    i += 1
                if longer:
                    new = display[min(longer, key=len)]
            if new != d.generic_name:
                changes.append((d, new))

        for d, new in changes:
            backup['drugs'].setdefault(d.pk, d.generic_name)
            MasterDrug.objects.filter(pk=d.pk).update(
                generic_name=new, generic_name_normalized=normalize_text(new))
        self.stdout.write(self.style.MIGRATE_HEADING(f'أسماء علمية مقطوعة/فيها تركيز: {len(changes)}'))
        for d, new in changes[:show]:
            self.stdout.write(f'   {d.generic_name!r} -> {new!r}')
        return len(changes)

    # ---- steps 1-3: stray letter, strength, companies ----------------------
    def _clean_aliases(self, backup, show, with_strength=True):
        aliases = list(MasterDrugAlias.objects.select_related('master_drug').order_by('pk'))
        canon = {f: build_manufacturer_canon(getattr(a, f) for a in aliases) for f in COMPANY_FIELDS}

        taken = {(a.trade_name_normalized, a.manufacturer, a.master_drug_id): a.pk for a in aliases}
        changes = {}          # pk -> {field: (old, new)}
        collisions = []
        for a in aliases:
            name = clean_product_name(a.trade_name)
            strength = a.master_drug.strength.strip()
            if strength and not is_clean_strength(strength) and name != strength and name.endswith(strength):
                name = name[:-len(strength)].rstrip(' -')     # dirty strength merged in by an earlier run
            new = {'trade_name': merge_strength_into_name(name, a.master_drug.strength) if with_strength else name}
            for f in COMPANY_FIELDS:
                new[f] = canon[f].get(getattr(a, f).strip(), getattr(a, f)) if getattr(a, f) else ''
            diff = {f: (getattr(a, f), v) for f, v in new.items() if v != getattr(a, f)}
            if not diff:
                continue
            old_key = (a.trade_name_normalized, a.manufacturer, a.master_drug_id)
            new_key = (normalize_text(new['trade_name']), new['manufacturer'], a.master_drug_id)
            if new_key != old_key and taken.get(new_key, a.pk) != a.pk:
                collisions.append((a, new['trade_name'], new['manufacturer']))
                continue
            taken.pop(old_key, None)
            taken[new_key] = a.pk
            changes[a.pk] = diff

        by_kind = {'trade_name': [], 'manufacturer': [], 'sudan_agent': []}
        for diff in changes.values():
            for f, (old, new) in diff.items():
                by_kind[f].append((old, new))
        labels = {'trade_name': 'اسم تجاري', 'manufacturer': 'شركة مصنعة', 'sudan_agent': 'وكيل'}
        for f, items in by_kind.items():
            self.stdout.write(self.style.MIGRATE_HEADING(f'{labels[f]}: {len(items)} تغيير'))
            for old, new in items[:show]:
                self.stdout.write(f'   {old!r} -> {new!r}')
        distinct = {f: len({new for _, new in items}) for f, items in by_kind.items() if f != 'trade_name'}
        self.stdout.write(f'distinct canonical companies touched: {distinct}')
        self.stdout.write(f'collisions left for manual review: {len(collisions)}')
        for a, t, m in collisions[:show]:
            self.stdout.write(f'   drug #{a.master_drug_id} alias #{a.pk}: {a.trade_name!r}/{a.manufacturer!r} -> {t!r}/{m!r}')

        for pk, diff in changes.items():
            saved = backup['aliases'].setdefault(pk, {})
            for f, (old, _) in diff.items():
                saved.setdefault(f, old)          # keep the ORIGINAL value if step 0 touched it
            values = {f: new for f, (_, new) in diff.items()}
            if 'trade_name' in values:
                values['trade_name_normalized'] = normalize_text(values['trade_name'])
            MasterDrugAlias.objects.filter(pk=pk).update(**values)
        return len(changes)

    # ---- step 4 (opt-in): merge duplicate MasterDrug rows -------------------
    def _merge_duplicates(self, backup, show):
        from collections import defaultdict
        groups = defaultdict(list)
        for d in MasterDrug.objects.filter(status='active').annotate(n_alias=Count('aliases')):
            if d.generic_name_normalized:
                groups[(d.generic_name_normalized, d.dosage_form_normalized, d.strength_normalized)].append(d)
        dup_groups = [g for g in groups.values() if len(g) > 1]

        merged = moved = deleted = 0
        for g in dup_groups:
            g.sort(key=lambda d: (-d.n_alias, d.pk))
            survivor, others = g[0], g[1:]
            seen = {(a.trade_name_normalized, a.manufacturer)
                    for a in MasterDrugAlias.objects.filter(master_drug=survivor)}
            for d in others:
                for a in MasterDrugAlias.objects.filter(master_drug=d).order_by('pk'):
                    key = (a.trade_name_normalized, a.manufacturer)
                    if key in seen:       # same product listed twice -> keep one
                        backup['deleted'].append({f: getattr(a, f) for f in ALIAS_BACKUP_FIELDS})
                        a.delete()
                        deleted += 1
                    else:
                        backup['moved'].setdefault(a.pk, [d.pk, a.is_primary])
                        MasterDrugAlias.objects.filter(pk=a.pk).update(master_drug=survivor, is_primary=False)
                        seen.add(key)
                        moved += 1
                backup['merged'].append(d.pk)
                MasterDrug.objects.filter(pk=d.pk).update(status='merged', merged_into=survivor)
                merged += 1

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'دمج المكرر: {len(dup_groups)} مجموعة، {merged} دواء مدموج، {moved} اسم تجاري منقول، {deleted} اسم مكرر محذوف'))
        for g in dup_groups[:show]:
            d = g[0]
            self.stdout.write(f'   {d.generic_name!r} | {d.strength!r} | {d.dosage_form!r}: {len(g)} صفوف -> 1')
        return merged

    # ---- optional: AI verdict for rows without statistical evidence -----------
    def _ai_verdicts(self, pairs, batch_size=40):
        """pairs: [(A, B)] with A stored as scientific, B as trade -> list of 'ok'|'swap'|'unsure'|None."""
        import json as _json
        from apps.ai.services import _call_deepseek, _parse_json_array

        verdicts = [None] * len(pairs)
        for start in range(0, len(pairs), batch_size):
            chunk = pairs[start:start + batch_size]
            payload = [{'i': i, 'A': a, 'B': b} for i, (a, b) in enumerate(chunk)]
            prompt = (
                'لكل زوج أدناه، A مخزَّن حالياً كاسم علمي/مادة فعالة وB كاسم تجاري. '
                'أجب "ok" إذا كان A فعلاً الاسم العلمي وB الاسم التجاري، '
                'و"swap" إذا كان العكس (B هو الاسم العلمي/المادة الفعالة وA اسم تجاري)، '
                'و"unsure" إذا لم تكن متأكداً. أجب بمصفوفة JSON فقط بنفس الترتيب: '
                '[{"i": 0, "v": "ok"}, ...] بلا أي نص إضافي.\n' + _json.dumps(payload, ensure_ascii=False))
            raw = _call_deepseek([
                {'role': 'system', 'content': 'أنت صيدلاني خبير بأسماء الأدوية. أجب بمصفوفة JSON صحيحة فقط.'},
                {'role': 'user', 'content': prompt},
            ], max_tokens=min(3000, 40 + 25 * len(chunk)))
            parsed = _parse_json_array(raw)
            if parsed is None:
                self.stdout.write(self.style.WARNING(f'AI: unreadable answer for rows {start}-{start + len(chunk)} ({raw[:80]!r})'))
                continue
            for item in parsed:
                if isinstance(item, dict) and isinstance(item.get('i'), int) and 0 <= item['i'] < len(chunk):
                    verdicts[start + item['i']] = str(item.get('v')).strip().lower()
        return verdicts

    def _ask_ai(self, rows, show):
        """rows: [(MasterDrug, primary alias)] -> the subset the AI twice says is reversed:
        asked once as stored, then again with the two names in the opposite order — a swap
        is applied only when both answers agree."""
        pairs = [(d.generic_name, strip_trailing_strength(a.trade_name)) for d, a in rows]
        first = self._ai_verdicts(pairs)
        candidates = [i for i, v in enumerate(first)
                      if v == 'swap'
                      # "Abacavir Sulfate" vs "Abacavir" is a salt variant, not a brand/scientific pair
                      and name_match_key(pairs[i][0]).split(' ')[:1] != name_match_key(pairs[i][1]).split(' ')[:1]]
        second = self._ai_verdicts([(pairs[i][1], pairs[i][0]) for i in candidates])
        confirmed = [rows[i] for i, v in zip(candidates, second) if v == 'ok']
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'AI: سُئل عن {len(rows)} صف، رأى العكس في {len(candidates)}، أكّده التحقق المعاكس في {len(confirmed)}'))
        dropped = [rows[i] for i, v in zip(candidates, second) if v != 'ok']
        for d, a in dropped[:show]:
            self.stdout.write(f'   AI رُفض بعد التحقق: علمي {d.generic_name!r} / تجاري {a.trade_name!r}')
        return confirmed

    # ---- optional: same trade name, different dosage forms -------------------
    def _disambiguate_forms(self, backup, show):
        from collections import defaultdict
        groups = defaultdict(list)
        for a in MasterDrugAlias.objects.filter(master_drug__status='active').select_related('master_drug'):
            groups[a.trade_name_normalized].append(a)

        def words(form):
            return frozenset(normalize_text(form).split())

        changed = []
        for items in groups.values():
            if len({a.master_drug_id for a in items}) < 2:
                continue
            forms = {words(a.master_drug.dosage_form) for a in items if a.master_drug.dosage_form.strip()}
            # "Powder" and "Powder for Injection" are one family, not two products
            families = [f for f in forms if not any(f < other for other in forms)]
            if len(families) < 2:
                continue
            for a in items:
                form = a.master_drug.dosage_form.strip()
                if form and not words(form) <= words(a.trade_name):
                    changed.append((a, f'{a.trade_name} {form}'))

        taken = {(a.trade_name_normalized, a.manufacturer, a.master_drug_id) for items in groups.values() for a in items}
        done = 0
        for a, new in changed:
            key = (normalize_text(new), a.manufacturer, a.master_drug_id)
            if key in taken:
                continue
            backup['aliases'].setdefault(a.pk, {}).setdefault('trade_name', a.trade_name)
            MasterDrugAlias.objects.filter(pk=a.pk).update(trade_name=new, trade_name_normalized=normalize_text(new))
            taken.add(key)
            done += 1
        self.stdout.write(self.style.MIGRATE_HEADING(f'نفس الاسم بأشكال مختلفة: أُضيف الشكل لـ {done} اسم'))
        for a, new in changed[:show]:
            self.stdout.write(f'   {a.trade_name!r} -> {new!r}')

    def _restore(self, filename):
        try:
            backup = json.loads(Path(filename).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'Cannot read backup: {exc}')
        with transaction.atomic():
            for pk in backup.get('merged', []):
                MasterDrug.objects.filter(pk=pk).update(status='active', merged_into=None)
            for pk, (old_drug, was_primary) in backup.get('moved', {}).items():
                MasterDrugAlias.objects.filter(pk=int(pk)).update(master_drug_id=old_drug, is_primary=was_primary)
            for row in backup.get('deleted', []):
                MasterDrugAlias.objects.create(**row)
            for pk, name in backup['drugs'].items():
                MasterDrug.objects.filter(pk=int(pk)).update(
                    generic_name=name, generic_name_normalized=normalize_text(name))
            for pk, old in backup['aliases'].items():
                values = dict(old)
                if 'trade_name' in values:
                    values['trade_name_normalized'] = normalize_text(values['trade_name'])
                MasterDrugAlias.objects.filter(pk=int(pk)).update(**values)
        self.stdout.write(self.style.SUCCESS(
            f'Restored {len(backup["aliases"])} aliases, {len(backup["drugs"])} drugs.'))
