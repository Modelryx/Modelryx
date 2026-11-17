import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

interface LicenseSource {
  package?: string;
  version?: string;
  url: string;
  sha256: string;
}

// Keep the delivered notices tied to the exact locked runtime packages.
function thirdPartyNotices() {
  const licenseRoot = new URL("../licenses/third-party/", import.meta.url);
  const sources: Record<string, LicenseSource> = JSON.parse(
    readFileSync(new URL("sources.json", licenseRoot), "utf8"),
  );
  const lock: { packages: Record<string, { version: string; dev?: boolean }> } = JSON.parse(
    readFileSync(new URL("package-lock.json", import.meta.url), "utf8"),
  );
  const sections = [
    "Modelryx licenses and third-party notices",
    readFileSync(new URL("../LICENSE", import.meta.url), "utf8"),
  ];
  for (const [path, entry] of Object.entries(lock.packages)) {
    if (!path || entry.dev) continue;
    const name = path.replace(/^node_modules\//, "");
    const match = Object.entries(sources).find(
      ([, record]) => record.package === name && record.version === entry.version,
    );
    if (!match) throw new Error(`Missing license notice for ${name}@${entry.version}`);
  }
  for (const [file, record] of Object.entries(sources)) {
    if (!record.package && !file.startsWith("Draco-")) continue;
    const bytes = readFileSync(new URL(file, licenseRoot));
    if (createHash("sha256").update(bytes).digest("hex") !== record.sha256) {
      throw new Error(`License text hash mismatch: ${file}`);
    }
    sections.push(
      `${record.package ?? "Draco"} ${record.version ?? ""}\n${record.url}\n\n${bytes.toString("utf8")}`,
    );
  }
  const source = sections.join("\n\n========================================\n\n");
  return {
    name: "modelryx-third-party-notices",
    generateBundle() {
      this.emitFile({ type: "asset" as const, fileName: "third-party-licenses.txt", source });
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split("?")[0] !== "/third-party-licenses.txt") return next();
        res.setHeader("Content-Type", "text/plain; charset=utf-8");
        res.end(source);
      });
    },
  } satisfies import("vite").Plugin;
}
export default defineConfig({
  plugins: [react(), thirdPartyNotices()],
  server: { port: 5173, strictPort: true },
  build: {
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const path = id.replaceAll("\\", "/");
          if (path.includes("/node_modules/three/examples/")) return "three-addons";
          if (path.includes("/node_modules/three/")) return "three-core";
          return undefined;
        },
      },
    },
  },
});


