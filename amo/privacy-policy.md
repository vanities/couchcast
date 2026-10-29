Couchcast has no analytics and no server of its own. It talks to YouTube, or to the Couchcast helper app on your computer, only to do what you ask.

When you cast in "No ads, straight to Apple TV" mode, the extension gives the video's id, playback position and speed to the helper app on your computer, through Firefox's native messaging. The helper asks YouTube for the video's stream, like any video player does, and sends the stream's address and the start time to your Apple TV on your local network. If you turn on "use my Firefox YouTube login" (off by default) and YouTube's bot check blocks the helper, the helper asks YouTube once more using your YouTube cookies from Firefox.

When you come back to a YouTube tab whose video is playing on your Apple TV, the extension asks the helper on your computer where the TV is, so it can move the paused video in the tab to the same spot. That question doesn't leave your computer.

When you cast in "YouTube app on the TV" mode, the extension sends YouTube's TV-linking service the video's id, playlist id and start position, along with a random id, made when you install Couchcast, that lets your TV tell this browser apart from your phone. When you link a TV, it sends the code your TV shows, and it renews that link with YouTube before it expires. While the settings page is open, it asks YouTube whether your linked TVs are available. None of these requests carry your YouTube or Google cookies.

Your settings and linked TVs stay in Firefox's storage on your computer. Nothing is sent anywhere else.

This policy on the web: https://am2.biz/couchcast/privacy
Website and help: https://am2.biz/couchcast and https://am2.biz/couchcast/support
Source code: https://github.com/vanities/couchcast
