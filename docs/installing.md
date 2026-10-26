# Installing the banner

Each site's **Overview** in the admin UI shows **Install on your site**: the script tag for that site, with instructions for any site, WordPress and Shopify, and a copy button.

The tag looks like this:

```html
<script src="https://cmp.example.com/consent-loader.js"
        data-site-id="<site-id>"
        data-api-base="https://cmp.example.com"></script>
```

When the banner is served from a different address to the API, the tag also has `data-cdn-base`, which tells the loader where to fetch the rest of the banner. The admin UI works this out from the deployment's settings. See `CDN_BASE_URL` and `PUBLIC_API_URL` in the [deployment guide](deployment-guide.md#cdn--banner).

## Where it goes

The tag must be the **first `<script>` in `<head>`**, on every page, with no `async` or `defer`. The banner holds back other scripts and cookies until the visitor consents, so anything that runs before it can set cookies first.

### Any site

Paste the tag into your page template's `<head>`, before any other script.

### WordPress

Use a plugin for adding code to your site's header, such as WPCode, and paste the tag as the first thing in its header section. Or paste it straight after `<head>` in your theme's `header.php`.

If you use a caching or optimisation plugin that delays or combines JavaScript, exclude `consent-loader.js` from it.

### Shopify

In your Shopify admin, go to **Online Store** then **Themes**, open the menu next to your live theme and choose **Edit code**. Open `layout/theme.liquid`, paste the tag on the line straight after `<head>`, and save. Repeat for any other theme you publish.

## Domains

The banner only works on the site's domain, its additional domains and their subdomains. Consent from any other domain is rejected. The install guide lists the domains for the site.

## Checking it worked

Each site's **Overview** shows whether the banner is installed:

| State | Meaning |
| --- | --- |
| **Not detected** | The banner hasn't loaded on any of the site's pages yet. |
| **Installed** | The banner has loaded on one of the site's pages. The Overview shows when and where it was last seen. |
| **Problem found** | The last check found something wrong, and the banner hasn't been seen since. |

The **Sites** list shows the same state for each site.

### Automatic detection

The site is marked installed the first time a page on one of its domains loads the banner, usually within a minute. Pages on other domains don't count, so a copy of the snippet on another site doesn't mark yours installed. Only the page's address is kept, never its query string. Detection happens after the banner has been sent its configuration, so it doesn't slow the banner down.

### Check now

**Check now** loads the site's home page as a first-time visitor and reports on each of these:

| Check | What it means when it fails |
| --- | --- |
| The page loads | The page couldn't be reached from the internet, or returned an error. |
| The page is on one of this site's domains | The page redirected to a domain that isn't registered for the site, so the banner won't run there. |
| The ConsentOS script is on the page | `consent-loader.js` isn't in the page. A loader added by a tag manager still counts. |
| The script has this site's ID | `data-site-id` belongs to another site. |
| The script points at this ConsentOS server | `data-api-base` isn't this deployment's API. Only checked when `PUBLIC_API_URL` is set. |
| The script isn't loaded asynchronously | The tag has `async` or `defer`, so other scripts can run before it. |
| The script is the first in `<head>` | Another script runs before it, or it's outside `<head>`. |
| The banner fetched its settings | The banner couldn't reach the API, often because of the page's content security policy. |
| The banner appeared | The script ran but the banner didn't show. |
| This site's configuration is published | A warning only: visitors see the built-in banner until you publish. |

Each failure comes with a fix. The result is kept, so the next person to open the site sees it too. Owners, admins and editors can run a check.

### Consent checks

When the banner appears, **Check now** also visits the page again in a fresh browser each time: once to accept, once to reject and, when the site has Global Privacy Control on, once with the browser sending it. It then checks what happened against the site's published configuration:

| Check | What it means when it fails |
| --- | --- |
| No optional cookies are set before the visitor chooses | A cookie in a category other than necessary was set before any choice. |
| No trackers load before the visitor chooses | A known tracker, such as Meta or Hotjar, loaded before any choice. |
| Accepting is remembered | The banner didn't save the choice, so the visitor is asked again on every page. |
| Rejecting is remembered | As above, for Reject. |
| No optional cookies are set after Reject | An optional cookie was set even though the visitor rejected. |
| No trackers load after Reject | A known tracker loaded after the visitor rejected. |
| Consent Mode defaults are set before Google tags | The page set no Consent Mode defaults, or a Google tag or Tag Manager started before them. Only checked when Consent Mode is on, as are the rows below. |
| Consent Mode defaults to denied | `ad_storage`, `ad_user_data`, `ad_personalization` or `analytics_storage` didn't default to denied. |
| Google tags wait for the visitor's choice | A warning only: the default has no `wait_for_update`. |
| Accept grants Consent Mode consent | After Accept, the page didn't send an update granting all four types. |
| Reject keeps Consent Mode denied | After Reject, one of the four types was granted. |
| Google receives the visitor's choice | Google's own requests reported a different consent state, in their `gcs` parameter, from the visitor's choice. Inconclusive when the page makes no Google requests. |
| TCF reports no consent before the visitor chooses, and after Reject | The TCF API reported consent the visitor hadn't given. Only checked when TCF is on. |
| TCF reports consent after Accept | The TCF API still reported no consent after Accept. Only checked when TCF is on. |
| The GPP API responds | GPP is on, but `__gpp` is missing, hasn't loaded, or doesn't list the site's sections the GPP 1.1 way, with their section IDs (`7:usnat`). Only checked when GPP is on, as are the two rows below. |
| Accept is recorded in the GPP string | After Accept, the GPP string was missing, didn't follow the GPP specification, had no section for one the site uses, or still said the visitor opted out. |
| Reject is recorded in the GPP string | After Reject, the GPP string didn't say the visitor opted out of sale, sharing and targeted advertising. |
| A Global Privacy Control signal is recorded | A visit with Global Privacy Control on wasn't noted in the saved choice, wasn't honoured when the site honours it everywhere, or didn't set the GPP string's GPC flag. Only checked when GPC is on. |

The GPP string is decoded independently of the banner, to the IAB GPP specification, so a string other parties would misread fails the check.

A check shows **Couldn't check** when it couldn't see what it needed: for example, when the page didn't load or the banner's Reject button couldn't be found. It never counts as passed. A cookie that hasn't been classified yet also makes its check inconclusive, because there's no way to tell whether it's allowed, so review new cookies on the **Cookies** tab. Checks for features the site doesn't use, such as TCF, aren't shown.

A failed consent check marks the site **Problem found**, like a failed installation check.
