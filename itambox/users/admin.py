from django.contrib import admin
from django.contrib.auth.models import Group

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass
