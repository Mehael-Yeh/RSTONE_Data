import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:rstone_data_protocol_example/rstone_protocol.dart';
import 'package:test/test.dart';

Future<Map<String, String>> fixtureKeys() async {
  final value = jsonDecode(await File('../trusted_keys.json').readAsString());
  return Map<String, String>.from(value['keys']);
}

Future<Uint8List> fixtureRaw(int sequence) =>
    File('../distribution/manifests/${sequence.toString().padLeft(6, '0')}.json').readAsBytes();

Future<Uint8List> pointer(Uint8List raw) async {
  final manifest = jsonDecode(utf8.decode(raw));
  final name = manifest['sequence'].toString().padLeft(6, '0');
  return Uint8List.fromList(utf8.encode(jsonEncode({
    'protocolVersion': 1,
    'sequence': manifest['sequence'],
    'dataVersion': manifest['dataVersion'],
    'manifestPath': 'manifests/$name.json',
    'manifestSha256': await sha256Hex(raw),
    'signaturePath': 'manifests/$name.sig',
    'keyId': manifest['keyId'],
  })));
}

Future<VerifiedManifest> fixture(int sequence, {int highest = 0, String? accepted}) async {
  final raw = await fixtureRaw(sequence);
  return verifyManifest(
      latestBytes: await pointer(raw), manifestBytes: raw,
      signatureBytes: await File('../distribution/manifests/${sequence.toString().padLeft(6, '0')}.sig').readAsBytes(),
      trustedKeys: await fixtureKeys(), appVersion: '1.0.0',
      highestSequence: highest, acceptedManifestSha256: accepted);
}

Future<VerifiedManifest> signedTestManifest(void Function(Map<String, dynamic>) edit) async {
  final manifest = jsonDecode(utf8.decode(await fixtureRaw(1))) as Map<String, dynamic>;
  manifest['sequence'] = 3;
  edit(manifest);
  (manifest['files'] as List).sort((a, b) => (a['path'] as String).compareTo(b['path'] as String));
  final raw = Uint8List.fromList(utf8.encode('${jsonEncode(manifest)}\n'));
  final algorithm = Ed25519();
  final key = await algorithm.newKeyPair();
  final public = await key.extractPublicKey();
  final sig = await algorithm.sign(raw, keyPair: key);
  return verifyManifest(latestBytes: await pointer(raw), manifestBytes: raw,
      signatureBytes: Uint8List.fromList(ascii.encode('${base64.encode(sig.bytes)}\n')),
      trustedKeys: {'example-only': base64.encode(public.bytes)}, appVersion: '1.0.0');
}

void main() {
  test('Python-produced Ed25519 manifest verifies in Dart', () async {
    expect((await fixture(1)).files.length, 5);
    expect((await fixture(2)).sequence, 2);
  });
  test('only changed raw object is downloaded', () async {
    final first = await fixture(1), second = await fixture(2);
    final plan = planSync(first, second, first.files.map((f) => f.sha256).toSet());
    expect(plan.modified, ['产品列表/RS-DEMO1.md']);
    expect(plan.unchanged.length, 4);
    expect(plan.missingObjects.length, 1);
  });
  test('rename and move reuse original object', () async {
    final first = await fixture(1);
    final renamed = await signedTestManifest((manifest) {
      for (final record in manifest['files']) {
        if (record['path'] == '产品列表/RS-DEMO1.md') record['path'] = '产品列表/RS-RENAMED.md';
        if (record['path'] == '产品应用/示例方案.md') record['path'] = '产品应用/PC/示例方案.md';
      }
    });
    final plan = planSync(first, renamed, first.files.map((f) => f.sha256).toSet());
    expect(plan.added.length, 2);
    expect(plan.deleted.length, 2);
    expect(plan.missingObjects, isEmpty);
  });
  test('raw object hash and length both checked', () async {
    final manifest = await fixture(2);
    for (final record in manifest.files) {
      final raw = await File('../distribution/objects/${record.sha256}').readAsBytes();
      expect(await verifyObject(record, raw), isTrue);
      expect(await verifyObject(record, Uint8List.fromList([...raw, 10])), isFalse);
    }
  });
  test('modified raw manifest rejected before activation', () async {
    final raw = await fixtureRaw(2);
    await expectLater(verifyManifest(latestBytes: await pointer(raw),
        manifestBytes: Uint8List.fromList([...raw, 32]),
        signatureBytes: await File('../distribution/manifests/000002.sig').readAsBytes(),
        trustedKeys: await fixtureKeys(), appVersion: '1.0.0'), throwsFormatException);
  });
  test('forged signature rejected even with matching digest', () async {
    final raw = await fixtureRaw(2);
    await expectLater(verifyManifest(latestBytes: await pointer(raw), manifestBytes: raw,
        signatureBytes: Uint8List.fromList(ascii.encode('${base64.encode(List.filled(64, 0))}\n')),
        trustedKeys: await fixtureKeys(), appVersion: '1.0.0'), throwsFormatException);
  });
  test('unsigned latest sequence cannot override signed sequence', () async {
    final raw = await fixtureRaw(2);
    final latest = jsonDecode(utf8.decode(await pointer(raw))) as Map<String, dynamic>;
    latest['sequence'] = 9;
    latest['manifestPath'] = 'manifests/000009.json';
    latest['signaturePath'] = 'manifests/000009.sig';
    await expectLater(verifyManifest(latestBytes: Uint8List.fromList(utf8.encode(jsonEncode(latest))),
        manifestBytes: raw, signatureBytes: await File('../distribution/manifests/000002.sig').readAsBytes(),
        trustedKeys: await fixtureKeys(), appVersion: '1.0.0'), throwsFormatException);
  });
  test('reject rollback replay and accept identical sequence idempotently', () async {
    await expectLater(fixture(1, highest: 2), throwsFormatException);
    final second = await fixture(2);
    expect((await fixture(2, highest: 2, accepted: second.manifestSha256)).sequence, 2);
    await expectLater(fixture(2, highest: 2, accepted: '0' * 64), throwsFormatException);
  });
  test('unknown trust anchor and old app rejected', () async {
    final raw = await fixtureRaw(2), sig = await File('../distribution/manifests/000002.sig').readAsBytes();
    await expectLater(verifyManifest(latestBytes: await pointer(raw), manifestBytes: raw,
        signatureBytes: sig, trustedKeys: {}, appVersion: '1.0.0'), throwsFormatException);
    await expectLater(verifyManifest(latestBytes: await pointer(raw), manifestBytes: raw,
        signatureBytes: sig, trustedKeys: await fixtureKeys(), appVersion: '0.9.9'), throwsFormatException);
  });
  test('duplicate JSON keys and excessive nesting rejected', () {
    expect(() => parseUniqueJson(Uint8List.fromList(utf8.encode('{"a":1,"a":2}'))), throwsFormatException);
    expect(() => parseUniqueJson(Uint8List.fromList(utf8.encode('${'[' * 66}0${']' * 66}'))), throwsFormatException);
  });
  test('unsafe and non-NFC paths rejected', () {
    for (final path in ['/产品列表/RS1.md', '产品列表/../RS1.md', '产品列表/NUL.md',
      '产品TDS/RS1.tds.md', '产品应用/e\u0301.md']) {
      expect(() => validateLogicalPath(path), throwsFormatException);
    }
  });
  test('full Unicode case folding rejects colliding signed paths', () async {
    await expectLater(signedTestManifest((manifest) {
      final record = Map<String, dynamic>.from(manifest['files'][0]);
      manifest['files'].add({...record, 'path': '产品应用/Straße.md'});
      manifest['files'].add({...record, 'path': '产品应用/STRASSE.md'});
    }), throwsFormatException);
  });
  test('conflicting lengths for shared hash rejected', () async {
    await expectLater(signedTestManifest((manifest) {
      final record = Map<String, dynamic>.from(manifest['files'][0]);
      manifest['files'].add({...record, 'path': '产品应用/duplicate.md', 'size': record['size'] + 1});
    }), throwsFormatException);
  });
}
