package service

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
	"time"
)

const (
	TutorialIPv6Tag = "默认IPv6直连"
	TutorialHY2Tag  = "默认HY2直连"
)

// Only advertise an IPv6 endpoint belonging to an active server interface.
// Link-local and unique-local addresses must never become public share hosts.
func tutorialPublicIPv6() string {
	interfaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, address := range addresses {
			ip, _, err := net.ParseCIDR(address.String())
			if err == nil && tutorialIsPublicIPv6(ip) {
				return ip.String()
			}
		}
	}
	return ""
}

func tutorialIsPublicIPv6(ip net.IP) bool {
	return ip != nil && ip.To4() == nil && ip.IsGlobalUnicast() && !ip.IsPrivate()
}

func tutorialUDPPort(requested int, used map[int]bool) (int, error) {
	for range 64 {
		if requested != 0 && used[requested] {
			return 0, fmt.Errorf("preset port %d is already in use", requested)
		}
		connection, err := net.ListenPacket("udp4", net.JoinHostPort("0.0.0.0", strconv.Itoa(requested)))
		if err != nil {
			return 0, err
		}
		port := connection.LocalAddr().(*net.UDPAddr).Port
		_ = connection.Close()
		if !used[port] {
			return port, nil
		}
	}
	return 0, errors.New("cannot reserve a distinct HY2 UDP port")
}

// Reuse renewable panel certificate files; never export an insecure TLS flag.
// With no certificate, retain a disabled HY2 draft instead of breaking VLESS.
func tutorialHY2Stream(certFile, keyFile, publicAddress string) (string, bool, error) {
	tlsSettings := map[string]any{"minVersion": "1.3", "alpn": []string{"h3"}}
	enabled := certFile != "" && keyFile != ""
	if certFile != "" || keyFile != "" {
		pair, err := tls.LoadX509KeyPair(certFile, keyFile)
		if err != nil {
			return "", false, fmt.Errorf("HY2 certificate: %w", err)
		}
		leaf, err := x509.ParseCertificate(pair.Certificate[0])
		if err != nil {
			return "", false, err
		}
		now := time.Now()
		if now.Before(leaf.NotBefore) || now.After(leaf.NotAfter) {
			return "", false, errors.New("HY2 certificate is not currently valid")
		}
		candidates := []string{publicAddress}
		candidates = append(candidates, leaf.DNSNames...)
		for _, ip := range leaf.IPAddresses {
			candidates = append(candidates, ip.String())
		}
		serverName := ""
		for _, candidate := range candidates {
			if candidate != "" && !strings.Contains(candidate, "*") && leaf.VerifyHostname(candidate) == nil {
				serverName = candidate
				break
			}
		}
		if serverName == "" {
			return "", false, errors.New("HY2 certificate needs a usable DNS or IP subject alternative name")
		}
		tlsSettings["serverName"] = serverName
		tlsSettings["certificates"] = []any{map[string]any{
			"certificateFile": certFile, "keyFile": keyFile, "oneTimeLoading": false,
		}}
	}
	stream, err := json.Marshal(map[string]any{
		"network": "hysteria", "security": "tls",
		"hysteriaSettings": map[string]any{"version": 2}, "tlsSettings": tlsSettings,
	})
	return string(stream), enabled, err
}

func tutorialIPv6Stream(serverName string) (string, error) {
	stream, err := tutorialRealityStream(serverName)
	if err != nil {
		return "", err
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(stream), &parsed); err != nil {
		return "", err
	}
	parsed["sockopt"] = map[string]any{"V6Only": true}
	encoded, err := json.Marshal(parsed)
	return string(encoded), err
}
