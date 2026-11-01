package service

import (
	"encoding/json"
	"net"
	"slices"
	"testing"

	"github.com/xtls/xray-core/infra/conf"

	"github.com/mhsanaei/3x-ui/v3/internal/database"
	"github.com/mhsanaei/3x-ui/v3/internal/database/model"
)

func TestTutorialPresetCreatesFourSubscriptionNodeDefinitions(t *testing.T) {
	setupConflictDB(t)
	result, err := ApplyTutorialPreset(TutorialPresetOptions{PublicAddress: "203.0.113.10", RealityServerName: TutorialDefaultServerName})
	if err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	var inbounds []model.Inbound
	if err := db.Order("sub_sort_index").Find(&inbounds).Error; err != nil {
		t.Fatal(err)
	}
	if len(inbounds) != 4 || inbounds[0].Tag != TutorialDirectTag || inbounds[3].Tag != TutorialResidentialTag {
		t.Fatalf("unexpected inbounds: %+v", inbounds)
	}
	if result.DirectPort == result.ResidentialPort || result.SubscriptionID == "" {
		t.Fatalf("invalid result: %+v", result)
	}
	privateKeys := map[string]bool{}
	clientIDs := map[string]bool{}
	for _, inbound := range []model.Inbound{inbounds[0], inbounds[3]} {
		if !inbound.Enable || inbound.ShareAddr != "203.0.113.10" {
			t.Fatal("node is disabled or has wrong public address")
		}
		clients, err := (&InboundService{}).GetClients(&inbound)
		if err != nil {
			t.Fatal(err)
		}
		if len(clients) != 1 || clients[0].Email != "01" || clients[0].SubID != result.SubscriptionID {
			t.Fatal("nodes do not share a subscription")
		}
		clientIDs[clients[0].ID] = true
		var stream map[string]any
		if err := json.Unmarshal([]byte(inbound.StreamSettings), &stream); err != nil {
			t.Fatal(err)
		}
		reality := stream["realitySettings"].(map[string]any)
		privateKeys[reality["privateKey"].(string)] = true
		names := reality["serverNames"].([]any)
		if reality["target"] != TutorialDefaultServerName+":443" || len(names) != 1 || names[0] != TutorialDefaultServerName || reality["minClientVer"] != "1.0.0" {
			t.Fatal("unexpected default REALITY target, SNI or minimum client version")
		}
		// Validate the exact wire shape against the embedded pinned Xray builder.
		encoded, _ := json.Marshal(inbound.GenXrayInboundConfig())
		var wire conf.InboundDetourConfig
		if err := json.Unmarshal(encoded, &wire); err != nil {
			t.Fatal(err)
		}
		if _, err := wire.Build(); err != nil {
			t.Fatalf("core rejects preset inbound: %v", err)
		}
	}
	if len(privateKeys) != 2 || len(clientIDs) != 1 {
		t.Fatal("nodes must share one client identity and retain independent REALITY keys")
	}
	listed, err := (&ClientService{}).List()
	if err != nil || len(listed) != 1 || listed[0].Email != "01" || len(listed[0].InboundIds) != 4 {
		t.Fatalf("client list must show one client attached to both nodes: %+v, %v", listed, err)
	}
	for _, inbound := range inbounds {
		if !slices.Contains(listed[0].InboundIds, inbound.Id) {
			t.Fatal("default client attachment is missing")
		}
	}
	for _, settingKey := range []string{"subEnable", "subClashEnable"} {
		value, err := (&SettingService{}).getString(settingKey)
		if err != nil || value != "true" {
			t.Fatalf("%s was not enabled: %q %v", settingKey, value, err)
		}
	}
	template, _ := (&SettingService{}).GetXrayConfigTemplate()
	var parsed map[string]any
	_ = json.Unmarshal([]byte(template), &parsed)
	rules := parsed["routing"].(map[string]any)["rules"].([]any)
	if len(rules) != 5 || rules[0].(map[string]any)["outboundTag"] != "api" {
		t.Fatal("default protective rules were lost")
	}
	if rules[4].(map[string]any)["outboundTag"] != TutorialSocksTag {
		t.Fatal("residential route does not select SOCKS")
	}
	obs := parsed["outbounds"].([]any)
	if obs[0].(map[string]any)["tag"] != "direct" {
		t.Fatal("default outbound was changed")
	}
	socks := obs[len(obs)-1].(map[string]any)
	if socks["settings"].(map[string]any)["address"] != "127.0.0.1" {
		t.Fatal("residential placeholder is not local")
	}
}

func TestTutorialPresetRepeatedApplyPreservesEditsAndDeletedNode(t *testing.T) {
	setupConflictDB(t)
	first, err := ApplyTutorialPreset(TutorialPresetOptions{RealityServerName: TutorialDefaultServerName})
	if err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	if err := db.Where("tag = ?", TutorialDirectTag).Delete(&model.Inbound{}).Error; err != nil {
		t.Fatal(err)
	}
	if err := (&SettingService{}).saveSetting("subClashEnable", "false"); err != nil {
		t.Fatal(err)
	}
	originalTemplate, _ := (&SettingService{}).GetXrayConfigTemplate()
	var parsed map[string]any
	_ = json.Unmarshal([]byte(originalTemplate), &parsed)
	for _, raw := range parsed["outbounds"].([]any) {
		ob := raw.(map[string]any)
		if ob["tag"] == TutorialSocksTag {
			ob["settings"] = map[string]any{"address": "192.0.2.10", "port": 1080, "user": "saved-user", "pass": "saved-password"}
		}
	}
	modified, _ := json.Marshal(parsed)
	if err := (&SettingService{}).saveSetting("xrayTemplateConfig", string(modified)); err != nil {
		t.Fatal(err)
	}
	second, err := ApplyTutorialPreset(TutorialPresetOptions{DirectPort: first.ResidentialPort})
	if err != nil {
		t.Fatal(err)
	}
	if !second.AlreadyApplied || second.SubscriptionID != first.SubscriptionID {
		t.Fatal("preset was not idempotent")
	}
	actual, _ := (&SettingService{}).GetXrayConfigTemplate()
	if actual != string(modified) {
		t.Fatal("saved residential credentials were overwritten")
	}
	value, _ := (&SettingService{}).getString("subClashEnable")
	if value != "false" {
		t.Fatal("user's subscription setting was overwritten")
	}
	var count int64
	db.Model(&model.Inbound{}).Count(&count)
	if count != 3 {
		t.Fatal("deleted node was recreated")
	}
}

func TestTutorialPresetLegacyMarkerPreservesEmptyPanel(t *testing.T) {
	setupConflictDB(t)
	db := database.GetDB()
	// An old installation may have intentionally deleted all its nodes.
	saved := TutorialPresetResult{Version: "3.6.0-preset.3", DirectPort: 443, ResidentialPort: 8443, SubscriptionID: "saved-subscription"}
	encoded, err := json.Marshal(saved)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&model.Setting{Key: tutorialLegacyPresetMarker, Value: string(encoded)}).Error; err != nil {
		t.Fatal(err)
	}
	originalTemplate, _ := (&SettingService{}).GetXrayConfigTemplate()
	result, err := ApplyTutorialPreset(TutorialPresetOptions{DirectPort: -1})
	if err != nil || !result.AlreadyApplied || result.SubscriptionID != saved.SubscriptionID || result.Version != saved.Version {
		t.Fatalf("legacy initialization must remain complete: %+v, %v", result, err)
	}
	for _, table := range []any{&model.Inbound{}, &model.ClientRecord{}} {
		var count int64
		if err := db.Model(table).Count(&count).Error; err != nil || count != 0 {
			t.Fatalf("deleted legacy records were recreated: %d, %v", count, err)
		}
	}
	actual, _ := (&SettingService{}).GetXrayConfigTemplate()
	if actual != originalTemplate {
		t.Fatal("legacy template was overwritten")
	}
	var count int64
	if err := db.Model(&model.Setting{}).Where("key = ?", TutorialPresetMarker).Count(&count).Error; err != nil || count != 0 {
		t.Fatal("upgrade created a new completion marker")
	}
}

func TestTutorialPresetRefusesExistingPanel(t *testing.T) {
	setupConflictDB(t)
	db := database.GetDB()
	if err := db.Create(&model.Inbound{Tag: "existing", Port: 12345, Protocol: model.VLESS, Settings: `{"clients":[]}`}).Error; err != nil {
		t.Fatal(err)
	}
	if _, err := ApplyTutorialPreset(TutorialPresetOptions{RealityServerName: TutorialDefaultServerName}); err == nil {
		t.Fatal("preset modified an existing panel")
	}
	var count int64
	db.Model(&model.Inbound{}).Count(&count)
	if count != 1 {
		t.Fatal("existing panel changed")
	}
	db.Model(&model.Setting{}).Where("key = ?", TutorialPresetMarker).Count(&count)
	if count != 0 {
		t.Fatal("failed initialization left a completion marker")
	}
}

func TestTutorialPresetRollsBackOnPersistenceFailure(t *testing.T) {
	setupConflictDB(t)
	db := database.GetDB()
	if err := db.Exec(`CREATE TRIGGER preset_failure BEFORE INSERT ON settings WHEN NEW.key = 'tutorialPresetV1' BEGIN SELECT RAISE(ABORT, 'preset rollback test'); END`).Error; err != nil {
		t.Fatal(err)
	}
	if _, err := ApplyTutorialPreset(TutorialPresetOptions{RealityServerName: TutorialDefaultServerName}); err == nil {
		t.Fatal("expected a persistence failure")
	}
	for _, table := range []any{&model.Inbound{}, &model.ClientRecord{}, &model.ClientInbound{}} {
		var count int64
		if err := db.Model(table).Count(&count).Error; err != nil {
			t.Fatal(err)
		}
		if count != 0 {
			t.Fatal("partial preset was committed")
		}
	}
	value, _ := (&SettingService{}).getString("subClashEnable")
	if value != "false" {
		t.Fatal("partial settings were committed")
	}
}

func TestTutorialPresetPortConflictAndInvalidOptions(t *testing.T) {
	listener, err := net.Listen("tcp", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	port := listener.Addr().(*net.TCPAddr).Port
	if _, err := tutorialPresetPort(port, nil, map[int]bool{}); err == nil {
		t.Fatal("occupied port was accepted")
	}
	for _, opts := range []TutorialPresetOptions{{DirectPort: 1000, ResidentialPort: 1000}, {DirectPort: -1}, {PublicAddress: "https://example.com"}} {
		if err := validateTutorialPresetOptions(opts); err == nil {
			t.Fatal("invalid preset options were accepted")
		}
	}
}

func TestTutorialPresetRejectsMicrosoftServerNames(t *testing.T) {
	for _, host := range []string{"microsoft.com", "www.microsoft.com", "WWW.MICROSOFT.COM.", "login.microsoftonline.com", "www.bing.com", "a.azureedge.net", "office.com"} {
		if err := validateTutorialPresetOptions(TutorialPresetOptions{RealityServerName: host}); err == nil {
			t.Fatalf("Microsoft server name accepted: %s", host)
		}
	}
	for _, host := range []string{"", "www.samsung.com", "www.apple.com", "microsoft.com.example.org", "notmicrosoft.com"} {
		if err := validateTutorialPresetOptions(TutorialPresetOptions{RealityServerName: host}); err != nil {
			t.Fatalf("unrelated server name rejected: %s: %v", host, err)
		}
	}
	setupConflictDB(t)
	if _, err := ApplyTutorialPreset(TutorialPresetOptions{RealityServerName: "www.microsoft.com"}); err == nil {
		t.Fatal("initialization accepted Microsoft SNI")
	}
	for _, table := range []any{&model.Inbound{}, &model.ClientRecord{}, &model.ClientInbound{}} {
		var count int64
		database.GetDB().Model(table).Count(&count)
		if count != 0 {
			t.Fatal("rejected SNI left partial nodes or clients")
		}
	}
}

func TestTutorialPresetChoosesFastestFeasibleNonMicrosoftTarget(t *testing.T) {
	valid := &RealityScanResult{Host: "www.samsung.com", Target: "www.samsung.com:443", Feasible: true, LatencyMs: 12}
	cases := []*RealityScanResult{
		nil,
		{Host: "www.microsoft.com", Feasible: true, LatencyMs: 1},
		{Host: "login.live.com", Feasible: true, LatencyMs: 1},
		{Host: "unreachable.example", Feasible: false, LatencyMs: 0},
		{Host: "private.example", Feasible: true, PrivateTarget: true, LatencyMs: 1},
		{Host: "slower.example", Feasible: true, LatencyMs: 120},
		valid,
	}
	selected, err := tutorialSelectRealityTarget(cases)
	if err != nil || selected != valid {
		t.Fatalf("wrong REALITY target: %+v %v", selected, err)
	}
	if _, err := tutorialSelectRealityTarget(cases[:4]); err == nil {
		t.Fatal("unusable targets were accepted")
	}
	for _, target := range tutorialRealityCandidates() {
		host, _, _ := splitRealityTarget(target)
		if tutorialMicrosoftServerName(host) {
			t.Fatal("Microsoft target remains in preset candidates")
		}
	}
}
