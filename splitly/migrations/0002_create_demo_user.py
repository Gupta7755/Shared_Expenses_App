from django.db import migrations

def create_demo_user(apps, schema_editor):
    User = apps.get_model('splitly', 'User')
    if not User.objects.filter(email='demo@share.app').exists():
        User.objects.create_superuser(
            username='demo@share.app',
            email='demo@share.app',
            password='demo1234',
            first_name='Demo',
            last_name='User',
            phone_number='1234567890',
            preferred_currency='USD'
        )

def remove_demo_user(apps, schema_editor):
    User = apps.get_model('splitly', 'User')
    User.objects.filter(email='demo@share.app').delete()

class Migration(migrations.Migration):

    dependencies = [
        ('splitly', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_demo_user, remove_demo_user),
    ]
