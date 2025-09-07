from django.core.management.base import BaseCommand
from django.db import transaction
from core.crypto import decrypt_string, encrypt_string
from licenses.models import License

class Command(BaseCommand):
    help = 'Rotate all encrypted fields in the database using the latest encryption keys.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Simulate key rotation without saving database changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        self.stdout.write("Scanning for encrypted fields in the database...")
        
        # Load all License objects (including soft-deleted)
        licenses = License.all_objects.all()
        total_licenses = licenses.count()
        
        self.stdout.write(f"Found {total_licenses} total licenses to inspect.")
        
        rotated_count = 0
        error_count = 0
        skipped_count = 0
        
        with transaction.atomic():
            for license_obj in licenses:
                raw_key = license_obj.product_key
                if not raw_key:
                    skipped_count += 1
                    continue
                
                if not raw_key.startswith("enc$"):
                    # Not encrypted yet (plaintext fallback)
                    decrypted_val = raw_key
                else:
                    try:
                        decrypted_val = decrypt_string(raw_key)
                    except Exception as e:
                        self.stderr.write(self.style.ERROR(
                            f"Failed to decrypt license {license_obj.pk} ({license_obj.name}): {e}"
                        ))
                        error_count += 1
                        continue
                
                # Check if decrypting returned the cipher string itself (meaning decryption failed fallback)
                if decrypted_val == raw_key and raw_key.startswith("enc$"):
                    self.stderr.write(self.style.ERROR(
                        f"Decryption failed or returned cipher value for license {license_obj.pk} ({license_obj.name}). Skipping to avoid data loss."
                    ))
                    error_count += 1
                    continue
                
                # Symmetrically encrypt with the current primary key (first key in the keyring)
                new_encrypted_key = encrypt_string(decrypted_val)
                
                if new_encrypted_key == raw_key:
                    # Key did not change (already encrypted with current primary key)
                    skipped_count += 1
                    continue
                
                rotated_count += 1
                
                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        f"[DRY RUN] Would rotate encryption key for license {license_obj.pk} ({license_obj.name})"
                    ))
                else:
                    # Bypass standard validations and save to database
                    License.all_objects.filter(pk=license_obj.pk).update(product_key=new_encrypted_key)
                    
            if dry_run:
                self.stdout.write(self.style.SUCCESS(
                    f"[DRY RUN] Simulation complete. "
                    f"Rotated: {rotated_count}, Skipped: {skipped_count}, Errors: {error_count}."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"Rotation complete! "
                    f"Successfully rotated: {rotated_count}, Skipped: {skipped_count}, Errors: {error_count}."
                ))
