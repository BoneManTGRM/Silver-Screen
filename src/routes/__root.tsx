import { createRootRoute, HeadContent, Outlet, Scripts } from "@tanstack/react-router";
import { Toaster } from "sonner";
import appCss from "../styles.css?url";

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      {
        title: "Silver-Screen — AI Movie Studio",
      },
      {
        name: "description",
        content:
          "Silver-Screen is a cinematic AI movie studio: write scripts, paint storyboards, render shorts, and package NFT film metadata.",
      },
      { name: "theme-color", content: "#080809" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      {
        rel: "preconnect",
        href: "https://fonts.gstatic.com",
        crossOrigin: "anonymous",
      },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&display=swap",
      },
    ],
  }),
  component: RootDocument,
});

function RootDocument() {
  return (
    <html lang="en" className="antialiased" suppressHydrationWarning>
      <head>
        <HeadContent />
      </head>
      <body className="min-h-dvh bg-bg text-foreground">
        <div className="film-grain" aria-hidden />
        <Outlet />
        <Toaster
          theme="dark"
          position="bottom-right"
          toastOptions={{
            style: {
              background: "#141416",
              border: "1px solid #2a2926",
              color: "#f0ebe3",
            },
          }}
        />
        <Scripts />
      </body>
    </html>
  );
}
