/**
 * ITAMbox JS entry point — imports all application modules for bundling.
 * Vendor JS (htmx, bootstrap, GridStack, TomSelect) is loaded separately
 * via <script> tags in base.html to preserve global assignments.
 */
import './state';
import './theme';
import './toasts';
import './slug-handling';
import './filter-toggle';
import './batch-actions';
import './object-selector';
import './table-config';
import './form-dirty';
import './form-submit-loading';
import './sidebar';
import './dashboard';
import './modal-handler';
import './audit';
