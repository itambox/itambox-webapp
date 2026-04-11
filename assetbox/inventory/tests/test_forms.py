from django.test import TestCase
from assets.models import Manufacturer, Category
from inventory.models import Accessory, Consumable, Kit, KitItem

def _create_category(name, component=False, accessory=False, consumable=False):
    applies_to = {}
    if component:
        applies_to['component'] = True
    if accessory:
        applies_to['accessory'] = True
    if consumable:
        applies_to['consumable'] = True
    slug = name.lower().replace(' ', '-')
    cat, _ = Category.objects.get_or_create(
        slug=slug,
        defaults={'name': name, 'applies_to': applies_to}
    )
    return cat

class InventoryFormValidationTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Apple', slug='apple')
        self.cat_accessory = _create_category('Mouse', accessory=True)
        self.cat_consumable = _create_category('Toner', consumable=True)
        self.kit = Kit.objects.create(name='New Employee Kit')

    def test_accessory_form_validation(self):
        from inventory.forms import AccessoryForm
        form = AccessoryForm(data={
            'manufacturer': self.manufacturer.pk,
            'name': 'Magic Mouse',
            'slug': 'apple-magic-mouse',
            'category': self.cat_accessory.pk,
        })
        self.assertTrue(form.is_valid())

        # Missing name
        form_invalid = AccessoryForm(data={
            'manufacturer': self.manufacturer.pk,
            'category': self.cat_accessory.pk,
        })
        self.assertFalse(form_invalid.is_valid())

    def test_consumable_form_validation(self):
        from inventory.forms import ConsumableForm
        form = ConsumableForm(data={
            'manufacturer': self.manufacturer.pk,
            'name': 'USB-C Cable',
            'slug': 'apple-usb-c-cable',
            'category': self.cat_consumable.pk,
        })
        self.assertTrue(form.is_valid())

    def test_kit_form_validation(self):
        from inventory.forms import KitForm
        form = KitForm(data={
            'name': 'Engineering Pack',
            'description': 'Laptops and docks',
        })
        self.assertTrue(form.is_valid())

    def test_kit_item_form_no_targets(self):
        from inventory.forms import KitItemForm
        form = KitItemForm(data={
            'kit': self.kit.pk,
            'qty': 1,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_kit_item_form_multiple_targets(self):
        from inventory.forms import KitItemForm
        acc = Accessory.objects.create(name='Key', manufacturer=self.manufacturer)
        con = Consumable.objects.create(name='Paper', manufacturer=self.manufacturer)
        form = KitItemForm(data={
            'kit': self.kit.pk,
            'accessory': acc.pk,
            'consumable': con.pk,
            'qty': 1,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
