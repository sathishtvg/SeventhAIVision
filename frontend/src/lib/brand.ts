/**
 * The product's own name, in one place.
 *
 * It was written two ways across the app — "Seventh AI Vision" on the sign-in
 * and password screens, "7th AI Vision" in the sidebar, the top bar, the
 * browser tab and the desktop notifications. Whichever a customer saw first,
 * the other looked like a different product.
 *
 * Keep this distinct from the two tenant-supplied names, which are not
 * interchangeable with it or with each other:
 *
 *   PRODUCT_NAME              this platform            "Seventh AI Vision"
 *   branding.company_name     the subscriber's label   "Demo"
 *   tenant.name               the subscriber's company "Demo"
 */
export const PRODUCT_NAME = 'Seventh AI Vision'
