from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_platform_staff'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200, verbose_name='العنوان')),
                ('action_type', models.CharField(
                    choices=[
                        ('create',  'إنشاء'),
                        ('update',  'تعديل'),
                        ('delete',  'حذف'),
                        ('confirm', 'تأكيد'),
                        ('cancel',  'إلغاء'),
                        ('login',   'تسجيل دخول'),
                        ('other',   'أخرى'),
                    ],
                    default='other',
                    max_length=50,
                    verbose_name='نوع الإجراء',
                )),
                ('details', models.TextField(blank=True, verbose_name='التفاصيل')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='عنوان IP')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='وقت النشاط')),
                ('tenant', models.ForeignKey(
                    db_index=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='user_activities',
                    to='core.tenant',
                    verbose_name='المشترك',
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='activities',
                    to='accounts.user',
                    verbose_name='المستخدم',
                )),
            ],
            options={
                'verbose_name': 'نشاط مستخدم',
                'verbose_name_plural': 'سجل الأنشطة',
                'db_table': 'user_activities',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='useractivity',
            index=models.Index(fields=['tenant', '-created_at'], name='act_tenant_date_idx'),
        ),
        migrations.AddIndex(
            model_name='useractivity',
            index=models.Index(fields=['tenant', 'user', '-created_at'], name='act_tenant_user_date_idx'),
        ),
    ]
