import { connection } from "next/server";

/**
 * Producer sign-in settings, read at request time from the container's environment so a
 * deployment can change them without rebuilding the image. All values are public: a
 * single-page app's client ID and API scope are visible to any browser that signs in.
 */
export async function GET() {
  await connection();
  const clientId = process.env.STUDIO_ENTRA_CLIENT_ID ?? "";
  const scope = process.env.STUDIO_API_SCOPE ?? "";
  const body =
    clientId && scope
      ? {
          clientId,
          scope,
          authority: process.env.STUDIO_ENTRA_AUTHORITY || "https://login.microsoftonline.com/common",
          access: process.env.STUDIO_PRODUCER_ACCESS === "role" ? "role" : "any_account",
        }
      : { clientId: null };
  return Response.json(body, { headers: { "Cache-Control": "no-store" } });
}
