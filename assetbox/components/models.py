from django.db import models
from django.urls import reverse
from core.models import BaseModel, ChangeLoggingMixin, AssetBoxModel
from core.mixins import TaggableMixin, AutoSlugMixin

class ComponentType(AutoSlugMixin, AssetBoxModel):
    """Catalog of physical hardware component models (e.g. Samsung 990 Pro 2TB SSD)."""
    slug_source = ('manufacturer__name', 'name')
    CATEGORY_RAM = 'ram'
    CATEGORY_STORAGE = 'storage'
    CATEGORY_GPU = 'gpu'
    CATEGORY_CPU = 'cpu'
    CATEGORY_NIC = 'nic'
    CATEGORY_OTHER = 'other'
    
    CATEGORY_CHOICES = [
        (CATEGORY_RAM, 'Memory (RAM)'),
        (CATEGORY_STORAGE, 'Storage (SSD/HDD)'),
        (CATEGORY_GPU, 'Graphics Card (GPU)'),
        (CATEGORY_CPU, 'Processor (CPU)'),
        (CATEGORY_NIC, 'Network Card (NIC)'),
        (CATEGORY_OTHER, 'Other'),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey('assets.Manufacturer', on_delete=models.PROTECT, related_name='component_types')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or part number")
    specs = models.CharField(max_length=255, blank=True, help_text="Specific capacity/speed details (e.g. 16GB DDR5 5600MHz)")
    description = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='component_types', blank=True)

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Component Type"
        verbose_name_plural = "Component Types"
        db_table = 'assets_componenttype'
        app_label = 'assets'

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('assets:componenttype_detail', kwargs={'pk': self.pk})


class ComponentInstance(TaggableMixin, ChangeLoggingMixin, BaseModel):
    """A physical component unit (e.g., a specific NVMe SSD with serial number) installed inside an Asset."""
    STATUS_INSTALLED = 'installed'
    STATUS_IN_STOCK = 'in_stock'
    STATUS_DEFECTIVE = 'defective'
    
    STATUS_CHOICES = [
        (STATUS_INSTALLED, 'Installed'),
        (STATUS_IN_STOCK, 'In Stock'),
        (STATUS_DEFECTIVE, 'Defective'),
    ]

    component_type = models.ForeignKey(ComponentType, on_delete=models.PROTECT, related_name='instances')
    serial_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="Physical serial number of the part")
    parent_asset = models.ForeignKey('assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='components', db_index=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_IN_STOCK)
    purchase_date = models.DateField(blank=True, null=True)
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='component_instances', blank=True)

    class Meta:
        ordering = ('component_type', 'serial_number')
        verbose_name = "Component"
        verbose_name_plural = "Components"
        db_table = 'assets_componentinstance'
        app_label = 'assets'

    def __str__(self):
        serial_part = f" [S/N: {self.serial_number}]" if self.serial_number else ""
        return f"{self.component_type.manufacturer.name} {self.component_type.name}{serial_part}"

    def get_absolute_url(self):
        return reverse('assets:componentinstance_detail', kwargs={'pk': self.pk})
