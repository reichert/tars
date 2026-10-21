import { describe, expect, it } from "vitest";
import capabilities from "../src-tauri/capabilities/default.json";

type Permission = string | { identifier: string; allow?: { url: string }[] };

describe("capabilities", () => {
  it("opener permits HTTPS issue providers without allowing other schemes", () => {
    const perms = capabilities.capabilities.flatMap((c) => c.permissions as Permission[]);
    const opener = perms.filter((p): p is Exclude<Permission, string> => typeof p !== "string" && p.identifier.startsWith("opener:"));
    expect(opener.map((p) => p.identifier)).toEqual(["opener:allow-open-url"]);
    expect(opener[0].allow).toEqual([{ url: "https://*" }]);
    // nenhuma permissão ampla do opener (ex.: `opener:default`, que abre qualquer URL)
    expect(perms.filter((p) => typeof p === "string" && p.startsWith("opener:"))).toEqual([]);
  });
});
