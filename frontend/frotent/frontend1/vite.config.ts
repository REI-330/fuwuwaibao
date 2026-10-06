import { existsSync } from "node:fs";
import vinext from "vinext";
import { defineConfig } from "vite";

// 说明：本仓库是从站点脚手架导出的 Next-on-Vite 前端。
// 脚手架原配置依赖 ./.openai/hosting.json、./build/sites-vite-plugin 与 ./worker/index.ts，
// 这三者未随源码一起提供。本地开发不需要 D1/R2 与 Cloudflare Worker 绑定，
// 因此这里把它们降级为可选：文件缺失时只跑 vinext，不再挂载 sites / cloudflare 插件。

const hasWorker = existsSync(new URL("./worker/index.ts", import.meta.url));

// macOS Seatbelt blocks FSEvents, so previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
  d1_databases: [] as {
    binding: string;
    database_name: string;
    database_id: string;
  }[],
  r2_buckets: [] as { binding: string; bucket_name: string }[],
};

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  const plugins = [vinext()];

  if (hasWorker) {
    // Wrangler snapshots its log path while the Cloudflare plugin is imported.
    const { cloudflare } = await import("@cloudflare/vite-plugin");
    plugins.push(
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        config: {
          ...localBindingConfig,
          d1_databases: localBindingConfig.d1_databases,
          r2_buckets: localBindingConfig.r2_buckets,
        },
      }) as never,
    );
  }

  return {
    server: isCodexSeatbeltSandbox
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
    plugins,
  };
});
