<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:a="http://www.w3.org/2005/Atom">
  <xsl:output method="html" encoding="utf-8" indent="yes"/>
  <xsl:template match="/a:feed">
    <html lang="en">
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title><xsl:value-of select="a:title"/> - Feed</title>
        <style>
          :root { color-scheme: light dark; }
          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            max-width: 46rem; margin: 0 auto; padding: 2rem 1.25rem 4rem;
            line-height: 1.5; color: CanvasText; background: Canvas;
          }
          header h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
          header p { margin: 0 0 2rem; opacity: 0.7; }
          article {
            padding: 1rem 0; border-top: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
          }
          article h2 { font-size: 1.05rem; margin: 0 0 0.25rem; }
          article h2 a { color: inherit; text-decoration: none; }
          article h2 a:hover { text-decoration: underline; }
          article .meta { font-size: 0.85rem; opacity: 0.65; margin: 0 0 0.35rem; }
          .pill {
            display: inline-block; font-size: 0.75rem; padding: 0.05rem 0.5rem;
            border: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
            border-radius: 999px; margin-left: 0.5rem; opacity: 0.85;
          }
        </style>
      </head>
      <body>
        <header>
          <h1><xsl:value-of select="a:title"/></h1>
          <p>Updated <xsl:value-of select="substring(a:updated, 1, 10)"/> &#8212; Atom feed. <a href="{a:link[@rel='self']/@href}">Feed URL</a> for your RSS reader.</p>
        </header>
        <xsl:for-each select="a:entry">
          <article>
            <h2>
              <a href="{a:link/@href}"><xsl:value-of select="a:title"/></a>
            </h2>
            <p class="meta">
              <xsl:value-of select="a:author/a:name"/> &#8212;
              <xsl:value-of select="substring(a:published, 1, 10)"/>
              <span class="pill"><xsl:value-of select="a:category/@term"/></span>
            </p>
          </article>
        </xsl:for-each>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
