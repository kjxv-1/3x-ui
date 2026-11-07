// Pure builders for 3x-ui install commands (script + Docker). No React/DOM.

export type InstallMethod = 'script' | 'docker';

export interface InstallOptions {
  method: InstallMethod;
  /** Preserved for callers; this branch always uses v3.10.2-preset-sort. */
  version: string;
  enableFail2ban: boolean;
  panelPort: string;
  webBasePath: string;
}

const REPO_RAW =
  'https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset-sort/deploy/preset/install.sh';
const IMAGE = 'ghcr.io/kjxv-1/3x-ui:v3.10.2-preset-sort';
const SOURCE = 'https://github.com/kjxv-1/3x-ui.git#codex/v3.10.2-preset-sort';

/** This branch deploys one maintained preset, independent of upstream latest. */
export function buildScriptCommand(_options: InstallOptions): string {
  return `bash <(curl -fLsS ${REPO_RAW})`;
}

/** A `docker run` command reflecting the chosen options. */
export function buildDockerRun(options: InstallOptions): string {
  const lines = ['docker run -itd'];
  if (options.enableFail2ban) {
    lines.push('  --cap-add=NET_ADMIN');
    lines.push('  --cap-add=NET_RAW');
  }
  lines.push(`  -e XRAY_VMESS_AEAD_FORCED=false`);
  lines.push(`  -e XUI_ENABLE_FAIL2BAN=${options.enableFail2ban ? 'true' : 'false'}`);
  if (options.panelPort.trim()) lines.push(`  -e XUI_PORT=${options.panelPort.trim()}`);
  if (options.webBasePath.trim())
    lines.push(`  -e XUI_INIT_WEB_BASE_PATH=${options.webBasePath.trim()}`);
  lines.push(`  -v $PWD/db/:/etc/x-ui/`);
  lines.push(`  -v $PWD/cert/:/root/cert/`);
  lines.push(`  --network=host`);
  lines.push(`  --restart=unless-stopped`);
  lines.push(`  --name 3x-ui`);
  lines.push(`  ${IMAGE}`);
  return `docker build -t ${IMAGE} ${SOURCE}\n\n` + lines.join(' \\\n');
}

/** A `docker-compose.yml` reflecting the chosen options. */
export function buildDockerCompose(options: InstallOptions): string {
  const env: string[] = [
    `      XRAY_VMESS_AEAD_FORCED: 'false'`,
    `      XUI_ENABLE_FAIL2BAN: '${options.enableFail2ban ? 'true' : 'false'}'`,
  ];
  if (options.panelPort.trim()) env.push(`      XUI_PORT: '${options.panelPort.trim()}'`);
  if (options.webBasePath.trim())
    env.push(`      XUI_INIT_WEB_BASE_PATH: '${options.webBasePath.trim()}'`);

  return [
    `services:`,
    `  3x-ui:`,
    `    image: ${IMAGE}`,
    `    pull_policy: build`,
    `    build: ${SOURCE}`,
    ...(options.enableFail2ban ? [`    cap_add: [NET_ADMIN, NET_RAW]`] : []),
    `    container_name: 3x-ui`,
    `    volumes:`,
    `      - ./db/:/etc/x-ui/`,
    `      - ./cert/:/root/cert/`,
    `    environment:`,
    ...env,
    `    network_mode: host`,
    `    restart: unless-stopped`,
  ].join('\n');
}
