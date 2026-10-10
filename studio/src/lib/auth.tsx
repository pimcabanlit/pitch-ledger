"use client";

import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { setTokenProvider } from "@/lib/api";

/**
 * Producer sign-in with Microsoft Entra ID (MSAL, redirect flow). The Studio asks for an
 * access token to the API's producer scope and sends it with every API call; the API
 * verifies it. When the deployment has no sign-in settings (local dev), sign-in is off and
 * the API decides (dev mode allows a local producer; otherwise it refuses).
 */
interface AuthConfig {
  clientId: string | null;
  scope?: string;
  authority?: string;
  access?: "role" | "any_account";
}

export type AuthStatus = "loading" | "off" | "signed_out" | "signed_in" | "error";

interface AuthState {
  status: AuthStatus;
  name: string;
  access: "role" | "any_account";
  error: string;
  signIn: () => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

let setup: Promise<{ pca: PublicClientApplication; config: AuthConfig } | null> | null = null;

/** One MSAL instance per page load (React may mount the provider twice in development). */
function initialise() {
  setup ??= (async () => {
    const config = (await (await fetch("/auth-config", { cache: "no-store" })).json()) as AuthConfig;
    if (!config.clientId || !config.scope) return null;
    const redirectUri = `${window.location.origin}/producer`;
    const pca = new PublicClientApplication({
      auth: {
        clientId: config.clientId,
        authority: config.authority,
        redirectUri,
        postLogoutRedirectUri: redirectUri,
      },
      cache: { cacheLocation: "sessionStorage" },
    });
    await pca.initialize();
    const result = await pca.handleRedirectPromise();
    const account = result?.account ?? pca.getActiveAccount() ?? pca.getAllAccounts()[0] ?? null;
    if (account) pca.setActiveAccount(account);
    return { pca, config };
  })();
  return setup;
}

function displayName(account: AccountInfo): string {
  return account.name || account.username || "producer";
}

export function ProducerAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<AuthState, "signIn" | "signOut">>({
    status: "loading",
    name: "",
    access: "any_account",
    error: "",
  });
  const [client, setClient] = useState<{ pca: PublicClientApplication; scope: string } | null>(null);

  useEffect(() => {
    let live = true;
    initialise().then(
      (ready) => {
        if (!live) return;
        if (!ready) {
          setState((s) => ({ ...s, status: "off" }));
          return;
        }
        const { pca, config } = ready;
        const scope = config.scope as string;
        const access = config.access ?? "any_account";
        setClient({ pca, scope });
        setTokenProvider(async () => {
          const account = pca.getActiveAccount();
          if (!account) return null;
          try {
            return (await pca.acquireTokenSilent({ scopes: [scope], account })).accessToken;
          } catch (error) {
            if (error instanceof InteractionRequiredAuthError) {
              await pca.acquireTokenRedirect({ scopes: [scope], account });
            }
            throw error;
          }
        });
        const account = pca.getActiveAccount();
        setState({
          status: account ? "signed_in" : "signed_out",
          name: account ? displayName(account) : "",
          access,
          error: "",
        });
      },
      (error: unknown) => {
        if (live) setState((s) => ({ ...s, status: "error", error: String(error) }));
      },
    );
    return () => {
      live = false;
    };
  }, []);

  const signIn = useCallback(() => {
    client?.pca.loginRedirect({ scopes: [client.scope], prompt: "select_account" });
  }, [client]);
  const signOut = useCallback(() => {
    setTokenProvider(null);
    client?.pca.logoutRedirect({ account: client.pca.getActiveAccount() });
  }, [client]);

  return <AuthContext.Provider value={{ ...state, signIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useProducerAuth(): AuthState {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useProducerAuth needs a ProducerAuthProvider");
  return auth;
}
