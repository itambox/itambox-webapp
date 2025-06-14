/**
 * Handles slug field auto-generation from name fields,
 * including adding refresh buttons to slug fields.
 */

document.addEventListener('DOMContentLoaded', function() {
    // Find all forms with both name and slug fields
    const forms = document.querySelectorAll('form');
    
    forms.forEach(form => {
        const nameField = form.querySelector('[name="name"]');
        const slugField = form.querySelector('[name="slug"]');
        
        if (nameField && slugField) {
            // Add refresh button after slug field
            addRefreshButton(slugField, nameField);
            
            // Add event listener to name field to auto-generate slug
            nameField.addEventListener('input', function() {
                // Only auto-generate if slug field is empty
                if (!slugField.value) {
                    slugField.value = slugify(nameField.value);
                }
            });
        }
    });
});

/**
 * Adds a refresh button after a slug field
 * 
 * @param {HTMLElement} slugField - The slug input field
 * @param {HTMLElement} nameField - The name input field to generate slug from
 */
function addRefreshButton(slugField, nameField) {
    // Create button container (input group)
    const inputGroup = document.createElement('div');
    inputGroup.classList.add('input-group');
    
    // Move slug field into the input group
    const parent = slugField.parentNode;
    parent.insertBefore(inputGroup, slugField);
    inputGroup.appendChild(slugField);
    
    // Create refresh button
    const refreshButton = document.createElement('button');
    refreshButton.setAttribute('type', 'button');
    refreshButton.classList.add('btn', 'btn-outline-secondary');
    refreshButton.innerHTML = '<i class="ti ti-refresh"></i>';
    refreshButton.title = 'Generate from name';
    
    // Add button to input group
    inputGroup.appendChild(refreshButton);
    
    // Add click event to refresh button
    refreshButton.addEventListener('click', function() {
        if (nameField.value) {
            slugField.value = slugify(nameField.value);
            // Focus the slug field
            slugField.focus();
        }
    });
}

/**
 * Converts a string to a slug (URL-friendly string)
 * 
 * @param {string} text - The text to convert to a slug
 * @returns {string} The slugified text
 */
function slugify(text) {
    return text.toString().toLowerCase()
        .replace(/\s+/g, '-')           // Replace spaces with -
        .replace(/[^\w\-]+/g, '')       // Remove all non-word chars
        .replace(/\-\-+/g, '-')         // Replace multiple - with single -
        .replace(/^-+/, '')             // Trim - from start of text
        .replace(/-+$/, '');            // Trim - from end of text
} 