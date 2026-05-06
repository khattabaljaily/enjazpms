import json
from pathlib import Path

from django.conf import settings


APP_SCHEMA_PATH = Path(__file__).resolve().parent / 'permissions_schema.json'
ROOT_SCHEMA_PATH = Path(settings.BASE_DIR) / 'perms.json'


def get_permission_schema_path():
    if APP_SCHEMA_PATH.exists():
        return APP_SCHEMA_PATH
    if ROOT_SCHEMA_PATH.exists():
        return ROOT_SCHEMA_PATH
    raise FileNotFoundError(
        'لم يتم العثور على ملف صلاحيات. يُرجى إنشاء ملف permissions_schema.json داخل app accounts أو perms.json في جذر المشروع.'
    )


def load_permission_schema():
    schema_path = get_permission_schema_path()
    with schema_path.open('r', encoding='utf-8-sig') as handle:
        return json.load(handle)


def get_permission_schema():
    return load_permission_schema()


def get_permission_keys():
    schema = load_permission_schema()
    keys = []
    for section in schema.values():
        keys.extend(section.keys())
    return keys


def get_all_permissions():
    return get_permission_keys()


def get_permission_choices():
    schema = load_permission_schema()
    flatten = []
    for section, permissions in schema.items():
        for key, label in permissions.items():
            flatten.append((key, label))
    return flatten


def access_allowed(user_group_id, perm):
    from .models import PermissionGroup

    try:
        group = PermissionGroup.objects.get(id=user_group_id)
        return bool(group.permissions.get(perm, False))
    except PermissionGroup.DoesNotExist:
        return False
