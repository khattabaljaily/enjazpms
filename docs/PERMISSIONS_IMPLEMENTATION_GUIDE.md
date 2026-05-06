# دليل تطبيق الصلاحيات على Views (Permission Decorators Guide)

## ملخص ما تم إنجازه

### ✅ تم الإنشاء والتطبيق:
1. **apps/accounts/decorators.py** - ملف الديكوريتورز الرئيسي يحتوي على:
   - `@require_permission('permission_key')` - للتحقق من صلاحية واحدة
   - `@require_any_permission('perm1', 'perm2', ...)` - للتحقق من أي صلاحية
   - `@require_all_permissions('perm1', 'perm2', ...)` - للتحقق من جميع الصلاحيات

2. **apps/accounts/permissions_schema.json** - ملف الصلاحيات المركزي يحتوي على جميع مفاتيح الصلاحيات:
   ```json
   {
       "العملاء": {
           "view_customers": "عرض بيانات العملاء",
           "add_customers": "إضافة عميل جديد",
           ...
       }
   }
   ```

3. **apps/accounts/permissions.py** - دوال مساعدة:
   - `load_permission_schema()` - تحميل الصلاحيات من JSON
   - `get_permission_keys()` - الحصول على جميع مفاتيح الصلاحيات
   - `access_allowed(user_group_id, perm)` - التحقق من صلاحية مستخدم

### ✅ تطبيقات تم تحديثها جزئياً:
- **apps/customers/views.py** - تم إضافة decorators على معظم الدوال
- **apps/items/views.py** - تم إضافة decorators على الدوال الرئيسية
- **apps/suppliers/views.py** - تم إضافة import وبدء التطبيق

---

## كيفية تطبيق هذا على باقي التطبيقات

### الخطوات الأساسية:

#### 1. في أعلى ملف views.py، أضف import:
```python
from apps.accounts.decorators import require_permission
```

#### 2. على كل دالة view رئيسية، أضف الديكوريتور:
```python
@login_required
@require_permission('permission_key')
def view_function(request):
    # الكود الحالي
```

#### 3. استخدم مفاتيح الصلاحيات من ملف JSON:

| التطبيق | الصفحة | المفاتيح |
|--------|---------|---------|
| **Customers** | customer_list | `view_customers` |
| | customer_create_api | `add_customers` |
| | customer_update_api | `change_customers` |
| | customer_delete_api | `delete_customers` |
| | customer_payments | `view_customer_payments` |
| | customer_payment_create_api | `add_customer_payments` |
| **Suppliers** | supplier_list | `view_suppliers` |
| | supplier_create_api | `add_suppliers` |
| | supplier_update_api | `change_suppliers` |
| | supplier_delete_api | `delete_suppliers` |
| **Items** | item_list | `view_items` |
| | item_create_api | `add_items` |
| | item_update_api | `change_items` |
| | item_delete_api | `delete_items` |
| | category_list | `view_categories` |
| | category_create_api | `add_categories` |
| | unit_list | `view_units` |
| | unit_create_api | `add_units` |
| **Stocks** | stock_list | `view_stocks` |
| | stock_create_api | `add_stocks` |
| | stock_quantities_list | `view_stock_quantities` |
| **Sales** | invoice_list | `view_sales` |
| | invoice_create | `add_sales` |
| | quote_list | `view_quotes` |
| | return_list | `view_sales_returns` |
| **Purchases** | order_list | `view_purchases` |
| | order_create | `add_purchases` |
| | return_list | `view_purchase_returns` |
| **Expenses** | expense_list | `view_expenses` |
| | expense_create | `add_expenses` |
| **Treasury** | treasury_list | `view_treasuries` |
| | treasury_create_api | `add_treasuries` |

---

## مثال تطبيق عملي

### قبل التحديث:
```python
@login_required
def supplier_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    # ... بقية الكود
```

### بعد التحديث:
```python
from apps.accounts.decorators import require_permission

@login_required
@require_permission('view_suppliers')
def supplier_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    # ... بقية الكود
```

---

## التطبيقات المتبقية التي تحتاج تحديث:

### 1. apps/suppliers/views.py
```
supplier_list → view_suppliers
supplier_create_api → add_suppliers
supplier_update_api → change_suppliers
supplier_delete_api → delete_suppliers
supplier_import_api → import_suppliers
supplier_export_api → export_suppliers
supplier_payments → view_supplier_payments
supplier_payment_create_api → add_supplier_payments
supplier_payment_cancel_api → cancel_supplier_payments
```

### 2. apps/stocks/views.py
```
stock_list → view_stocks
stock_create_api → add_stocks
stock_update_api → change_stocks
stock_delete_api → delete_stocks
opening_balance_list → view_stocks
stock_quantities_list → view_stock_quantities
```

### 3. apps/sales/views.py
```
invoice_list → view_sales
invoice_create → add_sales
invoice_edit → change_sales
invoice_delete_draft_ajax → delete_sales
return_list → view_sales_returns
return_create → add_sales_returns
quote_list → view_quotes
quote_create → add_quotes
```

### 4. apps/purchases/views.py
```
order_list → view_purchases
order_create → add_purchases
order_edit → change_purchases
order_cancel_ajax → delete_purchases
return_list → view_purchase_returns
return_create → add_purchase_returns
```

### 5. apps/expenses/views.py
```
expense_list → view_expenses
expense_create → add_expenses
expense_edit → change_expenses
expense_confirm_ajax → change_expenses
```

### 6. apps/treasury/views.py
```
treasury_list → view_treasuries
treasury_create_api → add_treasuries
treasury_update_api → change_treasuries
treasury_delete_api → delete_treasuries
```

---

## ملاحظات هامة

1. **Superuser و Tenant Admin**: يتم تجاوز فحوصات الصلاحيات تلقائياً
2. **الترتيب**: الديكوريتور يجب أن يأتي بعد `@login_required`
3. **مفاتيح JSON**: استخدم المفاتيح الموجودة في `permissions_schema.json` تماماً

---

## اختبار الصلاحيات

### للتحقق من الصلاحيات في View:
```python
if user.has_perm_key('view_customers'):
    # السماح بالعملية
```

### للتحقق قبل إرجاع API response:
```python
if not user.has_perm_key('view_customers'):
    return _json_error('ليس لديك صلاحية للوصول')
```

