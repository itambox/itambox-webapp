from rest_framework import serializers
from software.models import Software
# Import the *Nested* serializer from the new core location
from core.api.nested_serializers import NestedManufacturerSerializer 
from extras.api.serializers import TagSerializer # Reuse existing serializer

class SoftwareSerializer(serializers.ModelSerializer):
    # Use the Nested serializer from core
    manufacturer = NestedManufacturerSerializer(read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    
    class Meta:
        model = Software
        fields = (
            'id', 'name', 'manufacturer', 'description', 'tags', 
            'created_at', 'updated_at' # Assuming BaseModel provides these
        )
        # Consider adding depth or nested serializers for write operations later 