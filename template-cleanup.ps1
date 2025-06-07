# PowerShell Script to Restructure Django Templates Directory

# --- Configuration ---
# !!! IMPORTANT: Set this to the absolute path of your 'templates' directory !!!
$baseDir = "C:\Users\rene.rettig\HelheimCloud\Projekte\Coding\assetbox-webapp\assetbox\templates" # <-- UPDATE THIS PATH

# --- Safety Check ---
if (-not (Test-Path $baseDir)) {
    Write-Error "Base directory '$baseDir' not found. Please update the \$baseDir variable."
    exit 1
}

Write-Host "Starting template directory restructuring..."
Write-Host "Base Directory: $baseDir"
Write-Host "---"

# --- 1. Create New Directory Structure ---
Write-Host "Creating new directories..."

# Top-level
New-Item -ItemType Directory -Path "$baseDir\global_includes" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\core" -Force | Out-Null # Ensure core exists
New-Item -ItemType Directory -Path "$baseDir\core\includes" -Force | Out-Null

# Assets subdirs
New-Item -ItemType Directory -Path "$baseDir\assets\assets" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\assets\categories" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\assets\manufacturers" -Force | Out-Null
# assets/includes already exists

# Extras subdirs
New-Item -ItemType Directory -Path "$baseDir\extras\includes" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\extras\tags" -Force | Out-Null

# Organization subdirs
New-Item -ItemType Directory -Path "$baseDir\organization\includes" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\assetholders" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\locations" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\regions" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\sites" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\sitegroups" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\tenants" -Force | Out-Null
New-Item -ItemType Directory -Path "$baseDir\organization\tenantgroups" -Force | Out-Null

Write-Host "Directory creation complete."
Write-Host "---"

# --- 2. Move Files ---
Write-Host "Moving files..."

# Move files from old 'partials' to 'global_includes'
Write-Host "- Moving global includes..."
if (Test-Path "$baseDir\partials") {
    Move-Item -Path "$baseDir\partials\_sidebar.html" -Destination "$baseDir\global_includes\" -Force
    Move-Item -Path "$baseDir\partials\_toast.html" -Destination "$baseDir\global_includes\" -Force
    Move-Item -Path "$baseDir\partials\_topbar.html" -Destination "$baseDir\global_includes\" -Force
    Move-Item -Path "$baseDir\partials\pagination_links.html" -Destination "$baseDir\global_includes\" -Force
    Move-Item -Path "$baseDir\partials\pagination_per_page.html" -Destination "$baseDir\global_includes\" -Force
    # Note: Assuming tag.html was intended to be moved based on prior discussion, even if not in tree output
    # If tag.html is actually in extras/, the move to extras/tags/ below handles it.
    # If it was meant to be in partials/ and moved to extras/includes/, uncomment the next line.
    # Move-Item -Path "$baseDir\partials\tag.html" -Destination "$baseDir\extras\includes\" -Force
} else {
    Write-Warning "Source directory '$baseDir\partials' not found. Skipping moves from this location."
}


# Move files from old 'tables' to 'global_includes'
Write-Host "- Moving table includes..."
if (Test-Path "$baseDir\tables") {
    Move-Item -Path "$baseDir\tables\htmx_table.html" -Destination "$baseDir\global_includes\" -Force
} else {
    Write-Warning "Source directory '$baseDir\tables' not found. Skipping moves from this location."
}

# Move files within 'assets'
Write-Host "- Moving asset files..."
Move-Item -Path "$baseDir\assets\asset_confirm_delete.html" -Destination "$baseDir\assets\assets\" -Force
Move-Item -Path "$baseDir\assets\asset_detail.html" -Destination "$baseDir\assets\assets\" -Force
# Move-Item -Path "$baseDir\assets\asset_form.html" -Destination "$baseDir\assets\assets\" -Force # Keep commented if unused
# Move-Item -Path "$baseDir\assets\asset_list.html" -Destination "$baseDir\assets\assets\" -Force # Uncomment if asset_list.html exists

Move-Item -Path "$baseDir\assets\category_confirm_delete.html" -Destination "$baseDir\assets\categories\" -Force
Move-Item -Path "$baseDir\assets\category_detail.html" -Destination "$baseDir\assets\categories\" -Force
Move-Item -Path "$baseDir\assets\category_form.html" -Destination "$baseDir\assets\categories\" -Force
Move-Item -Path "$baseDir\assets\category_list.html" -Destination "$baseDir\assets\categories\" -Force

Move-Item -Path "$baseDir\assets\manufacturer_confirm_delete.html" -Destination "$baseDir\assets\manufacturers\" -Force
Move-Item -Path "$baseDir\assets\manufacturer_detail.html" -Destination "$baseDir\assets\manufacturers\" -Force
Move-Item -Path "$baseDir\assets\manufacturer_form.html" -Destination "$baseDir\assets\manufacturers\" -Force
Move-Item -Path "$baseDir\assets\manufacturer_list.html" -Destination "$baseDir\assets\manufacturers\" -Force

# Move asset checkout modal from assets/partials to assets/includes
if (Test-Path "$baseDir\assets\partials\asset_checkout_modal.html") {
    Move-Item -Path "$baseDir\assets\partials\asset_checkout_modal.html" -Destination "$baseDir\assets\includes\" -Force
} else {
     Write-Warning "Source file '$baseDir\assets\partials\asset_checkout_modal.html' not found."
}


# Move files within 'extras'
Write-Host "- Moving extras files..."
Move-Item -Path "$baseDir\extras\tag_confirm_delete.html" -Destination "$baseDir\extras\tags\" -Force
Move-Item -Path "$baseDir\extras\tag_form.html" -Destination "$baseDir\extras\tags\" -Force
Move-Item -Path "$baseDir\extras\tag_list.html" -Destination "$baseDir\extras\tags\" -Force
# Move-Item -Path "$baseDir\extras\tag_detail.html" -Destination "$baseDir\extras\tags\" -Force # Uncomment if tag_detail.html exists

# Move files within 'organization'
Write-Host "- Moving organization files..."
# AssetHolders (Note: Your tree output only showed detail, assuming others might exist or be created later)
Move-Item -Path "$baseDir\organization\assetholder_detail.html" -Destination "$baseDir\organization\assetholders\" -Force
# Move-Item -Path "$baseDir\organization\assetholder_confirm_delete.html" -Destination "$baseDir\organization\assetholders\" -Force
# Move-Item -Path "$baseDir\organization\assetholder_form.html" -Destination "$baseDir\organization\assetholders\" -Force
# Move-Item -Path "$baseDir\organization\assetholder_list.html" -Destination "$baseDir\organization\assetholders\" -Force

# Locations
Move-Item -Path "$baseDir\organization\location_confirm_delete.html" -Destination "$baseDir\organization\locations\" -Force
Move-Item -Path "$baseDir\organization\location_detail.html" -Destination "$baseDir\organization\locations\" -Force
Move-Item -Path "$baseDir\organization\location_form.html" -Destination "$baseDir\organization\locations\" -Force
Move-Item -Path "$baseDir\organization\location_list.html" -Destination "$baseDir\organization\locations\" -Force

# Regions
Move-Item -Path "$baseDir\organization\region_confirm_delete.html" -Destination "$baseDir\organization\regions\" -Force
Move-Item -Path "$baseDir\organization\region_detail.html" -Destination "$baseDir\organization\regions\" -Force
Move-Item -Path "$baseDir\organization\region_form.html" -Destination "$baseDir\organization\regions\" -Force
Move-Item -Path "$baseDir\organization\region_list.html" -Destination "$baseDir\organization\regions\" -Force

# Sites
Move-Item -Path "$baseDir\organization\site_confirm_delete.html" -Destination "$baseDir\organization\sites\" -Force
Move-Item -Path "$baseDir\organization\site_detail.html" -Destination "$baseDir\organization\sites\" -Force
Move-Item -Path "$baseDir\organization\site_form.html" -Destination "$baseDir\organization\sites\" -Force
Move-Item -Path "$baseDir\organization\site_list.html" -Destination "$baseDir\organization\sites\" -Force

# SiteGroups
Move-Item -Path "$baseDir\organization\sitegroup_confirm_delete.html" -Destination "$baseDir\organization\sitegroups\" -Force
Move-Item -Path "$baseDir\organization\sitegroup_detail.html" -Destination "$baseDir\organization\sitegroups\" -Force
Move-Item -Path "$baseDir\organization\sitegroup_form.html" -Destination "$baseDir\organization\sitegroups\" -Force
Move-Item -Path "$baseDir\organization\sitegroup_list.html" -Destination "$baseDir\organization\sitegroups\" -Force

# Tenants
Move-Item -Path "$baseDir\organization\tenant_confirm_delete.html" -Destination "$baseDir\organization\tenants\" -Force
Move-Item -Path "$baseDir\organization\tenant_detail.html" -Destination "$baseDir\organization\tenants\" -Force
Move-Item -Path "$baseDir\organization\tenant_form.html" -Destination "$baseDir\organization\tenants\" -Force
Move-Item -Path "$baseDir\organization\tenant_list.html" -Destination "$baseDir\organization\tenants\" -Force

# TenantGroups
Move-Item -Path "$baseDir\organization\tenantgroup_confirm_delete.html" -Destination "$baseDir\organization\tenantgroups\" -Force
Move-Item -Path "$baseDir\organization\tenantgroup_detail.html" -Destination "$baseDir\organization\tenantgroups\" -Force
Move-Item -Path "$baseDir\organization\tenantgroup_form.html" -Destination "$baseDir\organization\tenantgroups\" -Force
Move-Item -Path "$baseDir\organization\tenantgroup_list.html" -Destination "$baseDir\organization\tenantgroups\" -Force

# Move files within 'core' (Based on provided snippet path)
Write-Host "- Moving core files..."
# Check if the specific source path exists before moving
$corePartialSourceDir = "$baseDir\core\templates\core\partials" # Path from snippet
$corePartialSourceFile = "$corePartialSourceDir\table_config_modal.html"
$coreIncludeTargetDir = "$baseDir\core\includes"

if (Test-Path $corePartialSourceFile) {
    Move-Item -Path $corePartialSourceFile -Destination $coreIncludeTargetDir -Force
} else {
    Write-Warning "Source file '$corePartialSourceFile' not found. Check the path based on your actual structure."
    # Add alternative check if it might be in the top-level partials instead
    $altCorePartialSourceFile = "$baseDir\partials\table_config_modal.html"
     if (Test-Path $altCorePartialSourceFile) {
        Write-Host "  (Found table_config_modal.html in top-level partials instead, moving from there)"
        Move-Item -Path $altCorePartialSourceFile -Destination $coreIncludeTargetDir -Force
    }
}


Write-Host "File moving complete."
Write-Host "---"

# --- 3. Remove Obsolete Items ---
Write-Host "Removing obsolete directories and files..."

# Remove old top-level directories
if (Test-Path "$baseDir\partials") {
    Remove-Item -Path "$baseDir\partials" -Recurse -Force
    Write-Host "- Removed old 'partials' directory."
}
if (Test-Path "$baseDir\tables") {
    Remove-Item -Path "$baseDir\tables" -Recurse -Force
    Write-Host "- Removed old 'tables' directory."
}

# Remove old assets/partials directory
if (Test-Path "$baseDir\assets\partials") {
    Remove-Item -Path "$baseDir\assets\partials" -Recurse -Force
    Write-Host "- Removed old 'assets\partials' directory."
}

# Remove obsolete asset_checkout_form.html (assuming replaced by modal)
$obsoleteCheckoutForm = "$baseDir\assets\asset_checkout_form.html"
if (Test-Path $obsoleteCheckoutForm) {
    Remove-Item -Path $obsoleteCheckoutForm -Force
    Write-Host "- Removed obsolete '$obsoleteCheckoutForm'."
}


Write-Host "Cleanup complete."
Write-Host "---"

Write-Host "Template directory restructuring finished!"
Write-Host "IMPORTANT: Review the output for any warnings."
Write-Host "NEXT STEPS:"
Write-Host "1. Update render() calls in your Django views (assets/views.py, organization/views.py, etc.)."
Write-Host "2. Update {% include %} tags pointing to moved files (e.g., in base.html for sidebar, topbar, pagination, etc.)."
Write-Host "3. Commit the changes to your version control system."