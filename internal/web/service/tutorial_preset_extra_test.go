package service

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/xtls/xray-core/infra/conf"

	"github.com/mhsanaei/3x-ui/v3/internal/database"
	"github.com/mhsanaei/3x-ui/v3/internal/database/model"
)

func tutorialTestCertificate(t *testing.T) (string, string) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leaf := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "preset.example"},
		DNSNames: []string{"preset.example"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, leaf, leaf, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	certFile, keyFile := filepath.Join(root, "cert.pem"), filepath.Join(root, "key.pem")
	if err := os.WriteFile(certFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyFile, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER}), 0600); err != nil {
		t.Fatal(err)
	}
	return certFile, keyFile
}

func TestTutorialPresetIPv6AndHY2UseOneClientAndDistinctRoutes(t *testing.T) {
	setupConflictDB(t)
	cert, key := tutorialTestCertificate(t)
	settings := &SettingService{}
	for name, value := range map[string]string{"webCertFile": cert, "webKeyFile": key} {
		if err := settings.saveSetting(name, value); err != nil {
			t.Fatal(err)
		}
	}
	result, err := ApplyTutorialPreset(TutorialPresetOptions{PublicAddress: "127.0.0.1", PublicIPv6Address: "2001:db8::10", RealityServerName: TutorialDefaultServerName})
	if err != nil {
		t.Fatal(err)
	}
	if !result.IPv6Enabled || !result.HY2Enabled || len(result.Warnings) != 0 {
		t.Fatalf("unexpected readiness: %+v", result)
	}
	var inbounds []model.Inbound
	if err := database.GetDB().Order("sub_sort_index").Find(&inbounds).Error; err != nil {
		t.Fatal(err)
	}
	if len(inbounds) != 4 {
		t.Fatalf("got %d inbounds", len(inbounds))
	}
	ports := map[int]bool{}
	for _, inbound := range inbounds {
		if !inbound.Enable || inbound.ExcludeFromSub || ports[inbound.Port] {
			t.Fatalf("invalid inbound state: %s", inbound.Tag)
		}
		ports[inbound.Port] = true
		wireJSON, _ := json.Marshal(inbound.GenXrayInboundConfig())
		var wire conf.InboundDetourConfig
		if err := json.Unmarshal(wireJSON, &wire); err != nil {
			t.Fatal(err)
		}
		if _, err := wire.Build(); err != nil {
			t.Fatalf("%s: %v", inbound.Tag, err)
		}
	}
	v6, hy2 := inbounds[2], inbounds[3]
	if v6.Tag != TutorialIPv6Tag || v6.Listen != "::" || v6.ShareAddr != "2001:db8::10" || v6.ShareAddrStrategy != "custom" {
		t.Fatal("IPv6 endpoint fell back to IPv4")
	}
	if hy2.Tag != TutorialHY2Tag || hy2.Protocol != model.Hysteria {
		t.Fatal("missing HY2 inbound")
	}
	var stream map[string]any
	if err := json.Unmarshal([]byte(hy2.StreamSettings), &stream); err != nil {
		t.Fatal(err)
	}
	if stream["network"] != "hysteria" || stream["security"] != "tls" {
		t.Fatal("HY2 did not use QUIC and TLS")
	}
	if stream["tlsSettings"].(map[string]any)["serverName"] != "127.0.0.1" {
		t.Fatal("certificate hostname mismatch")
	}
	clients, err := (&ClientService{}).List()
	if err != nil || len(clients) != 1 || len(clients[0].InboundIds) != 4 {
		t.Fatalf("client attachments: %+v, %v", clients, err)
	}
	var auth string
	for _, inbound := range inbounds {
		clients, err := (&InboundService{}).GetClients(&inbound)
		if err != nil || len(clients) != 1 {
			t.Fatal("missing shared identity")
		}
		if len(clients[0].Auth) != 64 {
			t.Fatal("missing random HY2 authentication")
		}
		if auth != "" && auth != clients[0].Auth {
			t.Fatal("shared HY2 authentication changed")
		}
		auth = clients[0].Auth
	}
}

func TestTutorialPresetHY2MissingCertificateAndInvalidIPv6(t *testing.T) {
	stream, enabled, err := tutorialHY2Stream("", "", "203.0.113.10")
	if err != nil || enabled || stream == "" {
		t.Fatal("missing certificate must retain a disabled draft")
	}
	if _, _, err := tutorialHY2Stream("missing-cert", "missing-key", ""); err == nil {
		t.Fatal("invalid certificate accepted")
	}
	for _, address := range []string{"fe80::1", "fe80::1%9", "fd00::1", "::1", "192.0.2.1", "[2607:8700::2]", "2607:8700::/64"} {
		if err := validateTutorialPresetOptions(TutorialPresetOptions{PublicIPv6Address: address}); err == nil {
			t.Fatalf("invalid public IPv6 accepted: %s", address)
		}
	}
	if err := validateTutorialPresetOptions(TutorialPresetOptions{DirectPort: 12345, HY2Port: 12345}); err == nil {
		t.Fatal("duplicate protocol ports accepted")
	}
	udp, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer udp.Close()
	if _, err := tutorialUDPPort(udp.LocalAddr().(*net.UDPAddr).Port, map[int]bool{}); err == nil {
		t.Fatal("occupied UDP port accepted")
	}
}

func TestTutorialPresetRejectsOccupiedIPv6Port(t *testing.T) {
	var listenerConfig net.ListenConfig
	listener, err := listenerConfig.Listen(context.Background(), "tcp6", "[::1]:0")
	if err != nil {
		t.Skip("IPv6 sockets unavailable on this host")
	}
	defer listener.Close()
	if _, err := tutorialIPv6Port(listener.Addr().(*net.TCPAddr).Port, true, map[int]bool{}); err == nil {
		t.Fatal("occupied IPv6 port accepted")
	}
}
