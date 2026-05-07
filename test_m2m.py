#!/usr/bin/env python
"""
Manual test script to verify M2M relationship between User and PermissionGroup
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'PROJECT.settings')
django.setup()

from apps.accounts.models import User, PermissionGroup, Tenant

# Find or create test data
try:
    tenant = Tenant.objects.first()
    if not tenant:
        print("No tenant found. Create one first.")
        exit(1)
    
    # Create a test user
    user = User.objects.create_user(
        username='test_m2m_user',
        email='test@example.com',
        password='testpass123',
        tenant=tenant
    )
    print(f"✓ Created test user: {user.id}")
    
    # Create test groups
    group1 = PermissionGroup.objects.create(
        name='Test Group 1',
        tenant=tenant,
        permissions={}
    )
    group2 = PermissionGroup.objects.create(
        name='Test Group 2',
        tenant=tenant,
        permissions={}
    )
    print(f"✓ Created test groups: {group1.id}, {group2.id}")
    
    # Test M2M add
    print("\nTesting M2M relationship:")
    print(f"Before add - user groups: {list(user.permission_groups.values_list('id', flat=True))}")
    
    group1.users.add(user)
    group2.users.add(user)
    
    print(f"After add - user groups: {list(user.permission_groups.values_list('id', flat=True))}")
    
    # Refresh from DB
    user.refresh_from_db()
    print(f"After refresh - user groups: {list(user.permission_groups.values_list('id', flat=True))}")
    
    # Test M2M set
    print("\nTesting M2M set:")
    user.permission_groups.set([group1])
    print(f"After set([group1]) - user groups: {list(user.permission_groups.values_list('id', flat=True))}")
    
    # Test M2M clear
    print("\nTesting M2M clear:")
    user.permission_groups.clear()
    print(f"After clear() - user groups: {list(user.permission_groups.values_list('id', flat=True))}")
    
    # Cleanup
    user.delete()
    group1.delete()
    group2.delete()
    print("\n✓ Test completed successfully!")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
