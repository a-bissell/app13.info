# app13.info

A recreation of the original app13.info Flash game portal (circa 2010), now powered by [Ruffle](https://ruffle.rs) to play `.swf` games in modern browsers without Flash Player.

## How to Add Games

1. Get a `.swf` file for the game (try [BlueMaxima's Flashpoint](https://flashpointarchive.org/) or the [Wayback Machine](https://web.archive.org/))
2. Drop it into the `games/` folder
3. Name it to match the slug in the game link (e.g., `copter.swf`, `fishy.swf`, `bowman.swf`)

Click any game on the site — if the `.swf` isn't present yet, it will show you the exact filename it expects.

## Hosting

This site is designed for **GitHub Pages**. Push to the `main` branch and enable Pages in your repository settings (Settings > Pages > Source: Deploy from a branch > `main` / `/ (root)`).

Your site will be live at `https://a-bissell.github.io/app13.info/` or at your custom domain `https://app13.info`.

## Custom Domain

DNS is configured on Porkbun. To finish setup:

1. In your repo, go to Settings > Pages > Custom domain, enter `app13.info`
2. Check "Enforce HTTPS" once DNS propagates

## Tech

- Pure HTML/CSS (authentically retro)
- [Ruffle](https://ruffle.rs) Flash emulator loaded via CDN
- No build tools, no frameworks, no dependencies
