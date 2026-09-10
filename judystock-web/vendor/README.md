The K-line runtime is a pinned copy of the site's existing upstream asset:
https://www.hanstock.xyz/assets/index-CCs-RpRr.js

It is imported as text and patched by app/api/kline-runtime/route.ts. Keeping the
matching runtime and stylesheet in the release prevents an upstream asset outage
from blanking every chart. Embedded third-party license notices are preserved.

Matching stylesheet: public/kline-assets/index-CC6d_WMB.css.
