# Generated by Django 5.0.2 on 2025-01-12 16:13

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('job', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='job',
            name='title',
        ),
    ]
