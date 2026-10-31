import { useState } from "react";
import { ApiError, authMessage, mintToken } from "../api/client";
import type { AuthMode } from "../api/types";

type Props = {
  mode: AuthMode | null;
  onToken: (token: string, label: string) => void;
  onError: (message: string) => void;
  error: string;
};

const KEY_STORAGE = "review_api_key";
const SUB_STORAGE = "reviewer_name";

export function AuthPanel({ mode, onToken, onError, error }: Props) {
  const [reviewKey, setReviewKey] = useState(
    () => sessionStorage.getItem(KEY_STORAGE) || "",
  );
  const [sub, setSub] = useState(() => sessionStorage.getItem(SUB_STORAGE) || "");
  const [oidcRaw, setOidcRaw] = useState("");
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

  if (mode === null) {
    return (
      <section className="gate-card" aria-busy="true" aria-live="polite">
        <div className="skel skel-line" />
        <div className="skel skel-line short" />
        <div className="skel skel-block" />
        <p className="hint">正在确认鉴权模式</p>
      </section>
    );
  }

  if (mode === "oidc") {
    return (
      <form
        className="gate-card"
        onSubmit={(event) => {
          event.preventDefault();
          loginOidc();
        }}
      >
        <p className="eyebrow">IdP</p>
        <h2>使用人员令牌</h2>
        <p className="lede">本环境已关闭本地换票。粘贴身份提供方签发的访问令牌。</p>
        {error ? (
          <div className="alert alert-danger" role="alert">
            {error}
          </div>
        ) : null}
        <label className="field" htmlFor="oidc-token">
          <span>IdP 访问令牌</span>
          <input
            id="oidc-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="粘贴 access_token，可带 Bearer 前缀"
            aria-label="IdP 令牌"
            value={oidcRaw}
            onChange={(event) => setOidcRaw(event.target.value)}
          />
        </label>
        <button id="oidc-login" type="submit" className="btn primary block">
          使用令牌
        </button>
      </form>
    );
  }

  return (
    <form
      className="gate-card"
      onSubmit={(event) => {
        event.preventDefault();
        void loginMint();
      }}
    >
      <p className="eyebrow">人员身份</p>
      <h2>登录审核台</h2>
      <p className="lede">引导密钥只用来换取人员令牌。审核接口认的是这张令牌，不是密钥本身。</p>
      {error ? (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      ) : null}
      <label className="field" htmlFor="review-key">
        <span>审核引导密钥</span>
        <input
          id="review-key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="REVIEW_API_KEY"
          aria-label="审核引导密钥"
          value={reviewKey}
          onChange={(event) => setReviewKey(event.target.value)}
        />
      </label>
      <label className="field" htmlFor="reviewer">
        <span>审核人</span>
        <input
          id="reviewer"
          maxLength={64}
          autoComplete="username"
          placeholder="写入审计记录的 sub"
          aria-label="审核人"
          value={sub}
          onChange={(event) => setSub(event.target.value)}
        />
      </label>
      <button id="login" type="submit" className="btn primary block" disabled={busy}>
        {busy ? "正在换票" : "登录"}
      </button>
      <p className="hint">令牌保存在本标签页的会话里，关闭标签后需要重新登录。</p>
    </form>
  );
}
