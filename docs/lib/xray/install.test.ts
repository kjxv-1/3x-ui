import { describe, it, expect } from 'vitest';
import {
  buildScriptCommand,
  buildDockerRun,
  buildDockerCompose,
  type InstallOptions,
} from './install';

const base: InstallOptions = {
  method: 'script',
  version: '',
  enableFail2ban: true,
  panelPort: '',
  webBasePath: '',
};

describe('buildScriptCommand', () => {
  it('uses the maintained fork preset installer', () => {
    expect(buildScriptCommand(base)).toBe(
      'bash <(curl -fLsS https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset-sort/deploy/preset/install.sh)',
    );
  });

  it('keeps the preset independent of an old caller version', () => {
    const cmd = buildScriptCommand({ ...base, version: 'v3.4.1' });
    expect(cmd).toBe(
      'bash <(curl -fLsS https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset-sort/deploy/preset/install.sh)',
    );
  });

  it('does not switch this preset to a rolling development build', () => {
    const cmd = buildScriptCommand({ ...base, version: 'dev-latest' });
    expect(cmd).toBe(
      'bash <(curl -fLsS https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset-sort/deploy/preset/install.sh)',
    );
  });
});

describe('buildDockerRun', () => {
  it('reflects fail2ban, port, and base path options', () => {
    const cmd = buildDockerRun({
      ...base,
      enableFail2ban: false,
      panelPort: '8443',
      webBasePath: '/panel',
    });
    expect(cmd).toContain('XUI_ENABLE_FAIL2BAN=false');
    expect(cmd).toContain('XUI_PORT=8443');
    expect(cmd).toContain('XUI_INIT_WEB_BASE_PATH=/panel');
    expect(cmd).toContain('ghcr.io/kjxv-1/3x-ui:v3.10.2-preset-sort');
    expect(cmd).toContain('-v $PWD/db/:/etc/x-ui/');
  });

  it('omits unset port and path', () => {
    const cmd = buildDockerRun(base);
    expect(cmd).not.toContain('XUI_PORT');
    expect(cmd).not.toContain('XUI_INIT_WEB_BASE_PATH');
  });
});

describe('buildDockerCompose', () => {
  it('produces valid-looking compose with the image and volumes', () => {
    const yaml = buildDockerCompose({ ...base, panelPort: '2096' });
    expect(yaml).toContain('image: ghcr.io/kjxv-1/3x-ui:v3.10.2-preset-sort');
    expect(yaml).toContain('network_mode: host');
    expect(yaml).toContain("XUI_PORT: '2096'");
    expect(yaml).toContain('- ./db/:/etc/x-ui/');
  });
});
