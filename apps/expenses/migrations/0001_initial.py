import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        ('treasury', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ExpenseCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200, verbose_name='اسم التصنيف')),
                ('is_active', models.BooleanField(default=True, verbose_name='نشط')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='core.tenant', verbose_name='المتجر')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expensecategory_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expensecategory_updated', to=settings.AUTH_USER_MODEL, verbose_name='عُدل بواسطة')),
            ],
            options={
                'verbose_name': 'تصنيف مصروف',
                'verbose_name_plural': 'تصنيفات المصروفات',
                'db_table': 'expense_categories',
                'ordering': ['name'],
                'unique_together': {('tenant', 'name')},
            },
        ),
        migrations.CreateModel(
            name='Expense',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(blank=True, max_length=30, verbose_name='رقم المصروف')),
                ('description', models.CharField(max_length=500, verbose_name='الوصف')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='المبلغ')),
                ('expense_date', models.DateField(verbose_name='تاريخ المصروف')),
                ('payment_method', models.CharField(choices=[('cash', 'نقدي'), ('bank', 'بنكي')], default='cash', max_length=10, verbose_name='طريقة الدفع')),
                ('reference_number', models.CharField(blank=True, help_text='رقم الحوالة أو أمر الدفع عند التحويل البنكي', max_length=100, verbose_name='رقم المرجع')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('status', models.CharField(choices=[('draft', 'مسودة'), ('confirmed', 'مؤكد'), ('cancelled', 'ملغي')], default='draft', max_length=15, verbose_name='الحالة')),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='expenses', to='expenses.expensecategory', verbose_name='التصنيف')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='core.tenant', verbose_name='المتجر')),
                ('treasury', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='expenses', to='treasury.treasury', verbose_name='الخزينة / الحساب')),
                ('treasury_movement', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expense', to='treasury.treasurymovement', verbose_name='حركة الخزينة')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expense_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expense_updated', to=settings.AUTH_USER_MODEL, verbose_name='عُدل بواسطة')),
            ],
            options={
                'verbose_name': 'مصروف',
                'verbose_name_plural': 'المصروفات',
                'db_table': 'expenses',
                'ordering': ['-expense_date', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(fields=['tenant', 'status'], name='expense_tenant_status_idx'),
        ),
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(fields=['tenant', 'expense_date'], name='expense_tenant_date_idx'),
        ),
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(fields=['tenant', 'category'], name='expense_tenant_cat_idx'),
        ),
    ]
