import { useState } from "react";
import { ApiError, authMessage, mintToken } from "../api/client";
import type { AuthMode } from "../api/types";

type Props = {
  mode: AuthMode | null;
  token: string;
  sessionLabel?: string;
  onToken: (token: string, label: string) => void;
  onError: (message: string) => void;
  error: string;
};

const KEY_STORAGE = "review_api_key";
const SUB_STORAGE = "reviewer_name";

export function AuthPanel({
  mode,
  token,
  sessionLabel,
  onToken,
  onError,
  error,
}: Props) {
  const [reviewKey, setReviewKey] = useState(
    () => sessionStorage.getItem(KEY_STORAGE) || "",
  );
  const [sub, setSub] = useState(() => sessionStorage.getItem(SUB_STORAGE) || "");
  const [oidcRaw, setOidcRaw] = useState(() => token);
  const [busy, setBusy] = useState(false);

  async function loginMint() {
    onError("");
    const key = reviewKey.trim();
    const name = sub.trim();
    sessionStorage.setItem(KEY_STORAGE, key);
    sessionStorage.setItem(SUB_STORAGE, name);
    if (!key || !name) {
      onError("需要引导密钥与审核人");
      return;
    }
    setBusy(true);
    try {
      const body = await mintToken(key, name);
      onToken(body.access_token, body.sub || name);
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "review_token_invalid";
      onError(authMessage(code, "mint"));
    } finally {
      setBusy(false);
    }
  }

  function loginOidc() {
    onError("");
    let raw = oidcRaw.trim();
    if (raw.toLowerCase().startsWith("bearer ")) raw = raw.slice(7).trim();
    if (!raw) {
      onError("需要 IdP 令牌");
      return;
    }
    onToken(raw, "OIDC");
  }

  return (
    <div className="block">
      <h2>{mode === "oidc" ? "OIDC" : "身份"}</h2>
      {mode !== "oidc" ? (
        <div className="field">
          <input
            id="review-key"
            type="password"
            autoComplete="off"
            placeholder="REVIEW_API_KEY"
            aria-label="审核引导密钥"
            value={reviewKey}
            onChange={(e) => setReviewKey(e.target.value)}
          />
          <input
            id="reviewer"
            maxLength={64}
            placeholder="sub"
            aria-label="审核人"
            value={sub}
            onChange={(e) => setSub(e.target.value)}
          />
          <div className="actions">
            <button type="button" className="ghost" disabled={busy} onClick={loginMint}>
              登录
            </button>
          </div>
        </div>
      ) : (
        <div className="field">
          <input
            id="oidc-token"
            type="password"
            autoComplete="off"
            placeholder="Bearer access_token"
            aria-label="IdP 令牌"
            value={oidcRaw}
            onChange={(e) => setOidcRaw(e.target.value)}
          />
          <div className="actions">
            <button type="button" className="ghost" onClick={loginOidc}>
              使用令牌
            </button>
          </div>
        </div>
      )}
      {sessionLabel ? <p className="session">{sessionLabel}</p> : null}
      <p className="error">{error}</p>
    </div>
  );
}
