package service

import (
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"

	"github.com/mhsanaei/3x-ui/v3/internal/config"
	"github.com/mhsanaei/3x-ui/v3/internal/database"
	"github.com/mhsanaei/3x-ui/v3/internal/database/model"
)

var ErrTutorialPresetNonEmpty = errors.New("preset requires an empty panel; existing nodes and clients were preserved")

const (
	TutorialPresetMarker      = "tutorialPresetV1"
	TutorialDirectTag         = "默认直连"
	TutorialResidentialTag    = "默认住宅"
	TutorialSocksTag          = "默认"
	TutorialDefaultServerName = "www.samsung.com"

	// Read the old completion marker so upgrading never reapplies the preset.
	tutorialLegacyPresetMarker = "kjxvTutorialPresetV1"
)

type TutorialPresetOptions struct {
	PublicAddress     string
	DirectPort        int
	ResidentialPort   int
	IPv6Port          int
	HY2Port           int
	PublicIPv6Address string
	RealityServerName string
}

type TutorialPresetResult struct {
	Version           string   `json:"version"`
	DirectPort        int      `json:"directPort"`
	ResidentialPort   int      `json:"residentialPort"`
	IPv6Port          int      `json:"ipv6Port"`
	HY2Port           int      `json:"hy2Port"`
	IPv6Address       string   `json:"ipv6Address"`
	IPv6Enabled       bool     `json:"ipv6Enabled"`
	HY2Enabled        bool     `json:"hy2Enabled"`
	Warnings          []string `json:"warnings,omitempty"`
	SubscriptionID    string   `json:"subscriptionId"`
	AlreadyApplied    bool     `json:"alreadyApplied"`
	RealityServerName string   `json:"realityServerName"`
	RealityLatencyMs  int      `json:"realityLatencyMs"`
}

// ApplyTutorialPreset is an explicit installer operation, not a startup seeder.
// All records, settings and the completion marker commit together. A restart,
// upgrade or repeated install never regenerates credentials or deleted nodes.
func ApplyTutorialPreset(opts TutorialPresetOptions) (*TutorialPresetResult, error) {
	s := &SettingService{}
	panelPort, err := s.GetPort()
	if err != nil {
		return nil, err
	}
	subPort, err := s.GetSubPort()
	if err != nil {
		return nil, err
	}
	template, err := s.GetXrayConfigTemplate()
	if err != nil {
		return nil, err
	}
	cert, err := s.getString("webCertFile")
	if err != nil {
		return nil, err
	}
	key, err := s.getString("webKeyFile")
	if err != nil {
		return nil, err
	}
	result := &TutorialPresetResult{}
	err = database.GetDB().Transaction(func(tx *gorm.DB) error {
		var marker model.Setting
		findErr := tx.Where("key = ?", TutorialPresetMarker).First(&marker).Error
		if errors.Is(findErr, gorm.ErrRecordNotFound) {
			findErr = tx.Where("key = ?", tutorialLegacyPresetMarker).First(&marker).Error
		}
		if findErr == nil {
			if err := json.Unmarshal([]byte(marker.Value), result); err != nil {
				return fmt.Errorf("invalid preset marker: %w", err)
			}
			result.AlreadyApplied = true
			return nil
		}
		if !errors.Is(findErr, gorm.ErrRecordNotFound) {
			return findErr
		}
		for _, table := range []any{&model.Inbound{}, &model.ClientRecord{}} {
			var count int64
			if err := tx.Model(table).Count(&count).Error; err != nil {
				return err
			}
			if count != 0 {
				return ErrTutorialPresetNonEmpty
			}
		}
		if err := validateTutorialPresetOptions(opts); err != nil {
			return err
		}
		used := map[int]bool{panelPort: true, subPort: true, 62789: true, 11111: true}
		directPort, err := tutorialPresetPort(opts.DirectPort, nil, used)
		if err != nil {
			return err
		}
		used[directPort] = true
		residentialPort, err := tutorialPresetPort(opts.ResidentialPort, nil, used)
		if err != nil {
			return err
		}
		used[residentialPort] = true
		ipv6Address := opts.PublicIPv6Address
		if ipv6Address == "" {
			ipv6Address = tutorialPublicIPv6()
		}
		ipv6Port, err := tutorialIPv6Port(opts.IPv6Port, ipv6Address != "", used)
		if err != nil {
			return err
		}
		used[ipv6Port] = true
		hy2Port, err := tutorialUDPPort(opts.HY2Port, used)
		if err != nil {
			return err
		}
		hy2Stream, hy2Enabled, err := tutorialHY2Stream(cert, key, opts.PublicAddress)
		if err != nil {
			return err
		}
		serverName := opts.RealityServerName
		latencyMs := 0
		if serverName == "" {
			results, err := (&ServerService{}).ScanRealityTargets(strings.Join(tutorialRealityCandidates(), ","))
			if err != nil {
				return err
			}
			selected, err := tutorialSelectRealityTarget(results)
			if err != nil {
				return err
			}
			serverName, latencyMs = selected.Host, selected.LatencyMs
		}
		var xrayTemplate map[string]any
		if err := json.Unmarshal([]byte(template), &xrayTemplate); err != nil {
			return err
		}
		outbounds, ok := xrayTemplate["outbounds"].([]any)
		if !ok {
			return errors.New("preset requires an Xray outbound template")
		}
		for _, raw := range outbounds {
			ob, _ := raw.(map[string]any)
			if ob["tag"] == TutorialSocksTag {
				return errors.New("preset outbound tag is already in use")
			}
		}
		// This valid but deliberately unreachable placeholder keeps Xray running.
		// Its route always selects SOCKS; there is no direct fallback.
		outbounds = append(outbounds, map[string]any{
			"tag": TutorialSocksTag, "protocol": "socks",
			"settings": map[string]any{"address": "127.0.0.1", "port": 9},
		})
		xrayTemplate["outbounds"] = outbounds
		routing, ok := xrayTemplate["routing"].(map[string]any)
		if !ok {
			return errors.New("preset requires an Xray routing template")
		}
		rules, _ := routing["rules"].([]any)
		// The default API/private-address/BitTorrent protections retain priority.
		rules = append(rules,
			map[string]any{"type": "field", "inboundTag": []string{TutorialDirectTag, TutorialIPv6Tag, TutorialHY2Tag}, "outboundTag": "direct"},
			map[string]any{"type": "field", "inboundTag": []string{TutorialResidentialTag}, "outboundTag": TutorialSocksTag},
		)
		routing["rules"] = rules
		encodedTemplate, err := json.Marshal(xrayTemplate)
		if err != nil {
			return err
		}
		if err := (&XraySettingService{}).CheckXrayConfig(string(encodedTemplate)); err != nil {
			return err
		}
		subID, err := tutorialRandomHex(16)
		if err != nil {
			return err
		}
		now := time.Now().UnixMilli()
		// One portable client identity is attached to all preset inbounds.
		auth, err := tutorialRandomHex(32)
		if err != nil {
			return err
		}
		client := model.Client{
			ID: uuid.NewString(), Email: "01", Flow: "xtls-rprx-vision", Auth: auth,
			SubID: subID, Enable: true, CreatedAt: now, UpdatedAt: now,
		}
		var owner model.User
		if err := tx.Order("id").First(&owner).Error; err != nil {
			return err
		}
		for index, definition := range []struct {
			tag, name string
			port      int
		}{
			{TutorialDirectTag, "IPv4-直连", directPort},
			{TutorialIPv6Tag, "IPv6-直连", ipv6Port},
			{TutorialHY2Tag, "Hy2-直连", hy2Port},
			{TutorialResidentialTag, "住宅-待配置", residentialPort},
		} {
			settings, err := json.Marshal(map[string]any{"clients": []model.Client{client}, "decryption": "none", "fallbacks": []any{}})
			if err != nil {
				return err
			}
			stream, err := tutorialRealityStream(serverName)
			if err != nil {
				return err
			}
			inbound := &model.Inbound{
				UserId: owner.Id, Enable: true, Listen: "0.0.0.0", Port: definition.port,
				Protocol: model.VLESS, Tag: definition.tag, Remark: definition.name, Settings: string(settings),
				StreamSettings: stream, SubSortIndex: index + 1, TrafficReset: "never", TrafficResetDay: 1,
				ShareAddrStrategy: "node", Sniffing: `{"enabled":true,"destOverride":["http","tls","quic"],"routeOnly":true}`,
			}
			if opts.PublicAddress != "" {
				inbound.ShareAddrStrategy = "custom"
				inbound.ShareAddr = opts.PublicAddress
			}
			switch definition.tag {
			case TutorialIPv6Tag:
				inbound.Listen = "::"
				inbound.ShareAddrStrategy = "custom"
				inbound.ShareAddr = ipv6Address
				inbound.Enable = ipv6Address != ""
				inbound.ExcludeFromSub = !inbound.Enable
				inbound.StreamSettings, err = tutorialIPv6Stream(serverName)
				if err != nil {
					return err
				}
			case TutorialHY2Tag:
				inbound.Protocol = model.Hysteria
				encoded, err := json.Marshal(map[string]any{"version": 2, "clients": []model.Client{client}})
				if err != nil {
					return err
				}
				inbound.Settings = string(encoded)
				inbound.StreamSettings = hy2Stream
				inbound.Enable = hy2Enabled
				inbound.ExcludeFromSub = !hy2Enabled
			}
			if err := tx.Omit("ClientStats").Create(inbound).Error; err != nil {
				return err
			}
			if err := (&InboundService{}).AddClientStat(tx, inbound.Id, &client); err != nil {
				return err
			}
			if err := (&ClientService{}).SyncInbound(tx, inbound.Id, []model.Client{client}); err != nil {
				return err
			}
		}
		values := map[string]string{
			"xrayTemplateConfig": string(encodedTemplate), "subEnable": "true",
			"subClashEnable": "true", "devChannelEnable": "false", "remarkTemplate": "{{INBOUND}}",
		}
		if cert != "" && key != "" {
			values["subCertFile"] = cert
			values["subKeyFile"] = key
		}
		for settingKey, value := range values {
			if err := tx.Where("key = ?", settingKey).Assign(model.Setting{Value: value}).FirstOrCreate(&model.Setting{Key: settingKey, Value: value}).Error; err != nil {
				return err
			}
		}
		*result = TutorialPresetResult{
			Version: config.GetBaseVersion(), DirectPort: directPort, ResidentialPort: residentialPort,
			IPv6Port: ipv6Port, HY2Port: hy2Port, IPv6Address: ipv6Address, IPv6Enabled: ipv6Address != "", HY2Enabled: hy2Enabled,
			SubscriptionID: subID, RealityServerName: serverName, RealityLatencyMs: latencyMs,
		}
		if ipv6Address == "" {
			result.Warnings = append(result.Warnings, "IPv6 node is disabled and excluded from subscriptions: no public server IPv6 was found. Configure server IPv6 and this node's custom share address before enabling it.")
		}
		if !hy2Enabled {
			result.Warnings = append(result.Warnings, "HY2 node is disabled and excluded from subscriptions: no TLS certificate was configured. Add a valid certificate and matching SNI before enabling it.")
		}
		encodedResult, err := json.Marshal(result)
		if err != nil {
			return err
		}
		return tx.Create(&model.Setting{Key: TutorialPresetMarker, Value: string(encodedResult)}).Error
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

func validateTutorialPresetOptions(opts TutorialPresetOptions) error {
	for _, port := range []int{opts.DirectPort, opts.ResidentialPort, opts.IPv6Port, opts.HY2Port} {
		if port < 0 || port > 65535 {
			return errors.New("preset ports must be between 1 and 65535, or 0 for automatic selection")
		}
	}
	seen := map[int]bool{}
	for _, port := range []int{opts.DirectPort, opts.ResidentialPort, opts.IPv6Port, opts.HY2Port} {
		if port != 0 && seen[port] {
			return errors.New("preset nodes need different ports")
		}
		seen[port] = true
	}
	if opts.PublicIPv6Address != "" && !tutorialIsPublicIPv6(net.ParseIP(opts.PublicIPv6Address)) {
		return errors.New("preset IPv6 address must be a public IPv6 literal without brackets, prefix length or zone ID")
	}
	for _, host := range []string{opts.PublicAddress, opts.RealityServerName} {
		if host == "" || net.ParseIP(host) != nil {
			continue
		}
		if strings.ContainsAny(host, "/:\\ \t\r\n") || len(host) > 253 {
			return errors.New("preset address must be an IP or hostname without a port or URL scheme")
		}
		for _, ch := range host {
			if !strings.ContainsRune("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-", ch) {
				return errors.New("invalid preset hostname")
			}
		}
	}
	if tutorialMicrosoftServerName(opts.RealityServerName) {
		return errors.New("Microsoft domains are not supported by this tutorial preset; use a compatible non-Microsoft REALITY hostname")
	}
	return nil
}

// Compare DNS label boundaries so a related service or subdomain is blocked,
// while an unrelated hostname merely containing the word remains selectable.
func tutorialMicrosoftServerName(serverName string) bool {
	host := strings.TrimSuffix(strings.ToLower(serverName), ".")
	for _, domain := range []string{
		"microsoft.com", "microsoft.net", "microsoft.cn", "microsoftonline.com",
		"microsoftonline-p.com", "windows.com", "windows.net", "windowsupdate.com",
		"live.com", "outlook.com", "office.com", "office365.com", "sharepoint.com",
		"azure.com", "azure.net", "azureedge.net", "azurefd.net", "msedge.net",
		"bing.com", "msn.com", "xbox.com", "skype.com", "onedrive.com", "aka.ms",
		"msftconnecttest.com", "msftncsi.com", "visualstudio.com",
	} {
		if host == domain || strings.HasSuffix(host, "."+domain) {
			return true
		}
	}
	return false
}

func tutorialPresetPort(requested int, preferred []int, used map[int]bool) (int, error) {
	var listenerConfig net.ListenConfig
	candidates := preferred
	if requested != 0 {
		candidates = []int{requested}
	}
	for _, port := range candidates {
		if used[port] {
			continue
		}
		listener, err := listenerConfig.Listen(context.Background(), "tcp", net.JoinHostPort("0.0.0.0", strconv.Itoa(port)))
		if err == nil {
			_ = listener.Close()
			return port, nil
		}
	}
	if requested != 0 {
		return 0, fmt.Errorf("preset port %d is already in use", requested)
	}
	for range 64 {
		listener, err := listenerConfig.Listen(context.Background(), "tcp", "0.0.0.0:0")
		if err != nil {
			return 0, err
		}
		port := listener.Addr().(*net.TCPAddr).Port
		_ = listener.Close()
		if !used[port] {
			return port, nil
		}
	}
	return 0, errors.New("cannot reserve different random preset ports")
}

func tutorialRandomHex(size int) (string, error) {
	value := make([]byte, size)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return hex.EncodeToString(value), nil
}

func tutorialRealityStream(serverName string) (string, error) {
	key, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		return "", err
	}
	shortID, err := tutorialRandomHex(8)
	if err != nil {
		return "", err
	}
	stream, err := json.Marshal(map[string]any{
		"network": "tcp", "security": "reality", "tcpSettings": map[string]any{"header": map[string]any{"type": "none"}},
		"realitySettings": map[string]any{
			"show": false, "target": net.JoinHostPort(serverName, "443"), "xver": 0, "minClientVer": "1.0.0",
			"serverNames": []string{serverName}, "privateKey": base64.RawURLEncoding.EncodeToString(key.Bytes()), "shortIds": []string{shortID},
			"settings": map[string]any{"publicKey": base64.RawURLEncoding.EncodeToString(key.PublicKey().Bytes()), "fingerprint": "chrome", "serverName": serverName, "spiderX": "/"},
		},
	})
	return string(stream), err
}

// Reuse the panel scanner's feasibility checks; do not select an unreachable
// low-latency result or a Microsoft target, including service subdomains.
func tutorialRealityCandidates() []string {
	var targets []string
	for _, target := range defaultRealityScanCandidates {
		host, _, err := splitRealityTarget(target)
		if err == nil && !tutorialMicrosoftServerName(host) {
			targets = append(targets, target)
		}
	}
	return targets
}

func tutorialSelectRealityTarget(results []*RealityScanResult) (*RealityScanResult, error) {
	var best *RealityScanResult
	for _, result := range results {
		if result == nil || !result.Feasible || result.PrivateTarget || result.LatencyMs < 0 || tutorialMicrosoftServerName(result.Host) {
			continue
		}
		if err := validateTutorialPresetOptions(TutorialPresetOptions{RealityServerName: result.Host}); err != nil {
			continue
		}
		if best == nil || result.LatencyMs < best.LatencyMs {
			best = result
		}
	}
	if best == nil {
		return nil, errors.New("no compatible non-Microsoft REALITY target was reachable; check server connectivity or set XUI_PRESET_REALITY_SERVER_NAME to a verified target and retry")
	}
	return best, nil
}
