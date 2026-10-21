import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:unorm_dart/unorm_dart.dart' as unorm;

import 'src/casefold.dart';

const _maxInt = 9007199254740991;
const _roots = {'产品列表', '产品应用', '产品配方', '产品TDS'};
final _hashPattern = RegExp(r'^[0-9a-f]{64}$');
final _versionPattern = RegExp(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$');

void _need(bool condition, String message) {
  if (!condition) throw FormatException(message);
}

Map<String, dynamic> _object(dynamic value) {
  _need(value is Map<String, dynamic>, 'Expected JSON object');
  return value as Map<String, dynamic>;
}

bool _integer(dynamic value, [int minimum = 0]) =>
    value is int && value >= minimum && value <= _maxInt;

Future<String> sha256Hex(List<int> bytes) async => (await Sha256().hash(bytes))
    .bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

class DataFile {
  final String path;
  final String sha256;
  final int size;
  const DataFile(this.path, this.sha256, this.size);
}

class VerifiedManifest {
  final int sequence;
  final String dataVersion;
  final String manifestSha256;
  final List<DataFile> files;
  const VerifiedManifest._(this.sequence, this.dataVersion, this.manifestSha256, this.files);
}

List<int> _base64(String text, int size) {
  final bytes = base64.decode(text);
  _need(bytes.length == size && base64.encode(bytes) == text, 'Noncanonical Base64');
  return bytes;
}

int _utf8Compare(String a, String b) {
  final left = utf8.encode(a), right = utf8.encode(b);
  for (var i = 0; i < left.length && i < right.length; i++) {
    if (left[i] != right[i]) return left[i].compareTo(right[i]);
  }
  return left.length.compareTo(right.length);
}

void validateLogicalPath(String path) {
  final encoded = utf8.encode(path);
  _need(encoded.isNotEmpty && encoded.length <= 1024 && utf8.decode(encoded) == path,
      'Invalid UTF-8 path');
  _need(unorm.nfc(path) == path && !path.startsWith('/') && !path.contains('\\'),
      'Path must be NFC and relative POSIX');
  final parts = path.split('/');
  _need(parts.length >= 2 && _roots.contains(parts.first), 'Unknown namespace');
  for (final part in parts) {
    _need(part.isNotEmpty && !part.startsWith('.') && !part.endsWith('.') &&
        !part.endsWith(' ') && utf8.encode(part).length <= 240, 'Unsafe component');
    _need(!part.runes.any((r) => r < 32 || r == 127) &&
        !RegExp(r'[<>:"|?*]').hasMatch(part), 'Nonportable path');
    final stem = part.split('.').first.toUpperCase();
    _need(!RegExp(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$').hasMatch(stem), 'Reserved path');
  }
  _need(path.endsWith('.md'), 'Expected .md');
  if (parts.first == '产品TDS') {
    _need(parts.last.endsWith('.TDS.md') && parts.last.length > 7, 'Invalid TDS suffix');
  }
}

List<int> _version(String text) {
  _need(_versionPattern.hasMatch(text), 'Invalid SemVer');
  return text.split('.').map(int.parse).toList();
}

bool _compatible(String app, String minimum) {
  final a = _version(app), b = _version(minimum);
  for (var i = 0; i < 3; i++) {
    if (a[i] != b[i]) return a[i] > b[i];
  }
  return true;
}

/// trustedKeys comes from the installed application, never from this download.
/// Persist the returned sequence/hash together with the completed official generation.
Future<VerifiedManifest> verifyManifest({
  required Uint8List latestBytes,
  required Uint8List manifestBytes,
  required Uint8List signatureBytes,
  required Map<String, String> trustedKeys,
  required String appVersion,
  int highestSequence = 0,
  String? acceptedManifestSha256,
}) async {
  _need(latestBytes.length <= 16384 && manifestBytes.length <= 32 * 1024 * 1024 &&
      signatureBytes.length <= 512, 'Metadata too large');
  final latest = _object(parseUniqueJson(latestBytes));
  final manifest = _object(parseUniqueJson(manifestBytes));
  _need(latest['protocolVersion'] == 1 && latest['protocolVersion'] is int &&
      manifest['protocolVersion'] == 1 && manifest['protocolVersion'] is int, 'Unsupported protocol');
  _need(_integer(latest['sequence'], 1) && _integer(manifest['sequence'], 1), 'Invalid sequence');
  final sequence = latest['sequence'] as int;
  final name = sequence.toString().padLeft(6, '0');
  _need(latest['manifestPath'] == 'manifests/$name.json' &&
      latest['signaturePath'] == 'manifests/$name.sig', 'Unexpected metadata path');
  final digest = await sha256Hex(manifestBytes);
  _need(latest['manifestSha256'] == digest, 'Manifest digest mismatch');
  for (final field in ['protocolVersion', 'sequence', 'dataVersion', 'keyId']) {
    _need(latest[field] == manifest[field], 'Unsigned/signed metadata mismatch: $field');
  }
  final keyId = manifest['keyId'];
  _need(keyId is String && RegExp(r'^[A-Za-z0-9_-]{1,64}$').hasMatch(keyId) &&
      trustedKeys.containsKey(keyId), 'Unknown signing keyId');
  final sigText = ascii.decode(signatureBytes);
  _need(sigText.endsWith('\n') && !sigText.substring(0, sigText.length - 1).contains('\n'),
      'Signature must be one Base64 line with LF');
  final signature = Signature(_base64(sigText.substring(0, sigText.length - 1), 64),
      publicKey: SimplePublicKey(_base64(trustedKeys[keyId]!, 32), type: KeyPairType.ed25519));
  _need(await Ed25519().verify(manifestBytes, signature: signature), 'Invalid Ed25519 signature');
  _need(manifest['schemaVersion'] == 1 && manifest['schemaVersion'] is int, 'Unsupported schema');
  _need(manifest['minAppVersion'] is String &&
      _compatible(appVersion, manifest['minAppVersion'] as String), 'Application upgrade required');
  _need(manifest['dataVersion'] is String &&
      RegExp(r'^[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{3,}$').hasMatch(manifest['dataVersion']),
      'Invalid display version');
  final stamp = manifest['publishedAt'];
  _need(stamp is String && RegExp(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$').hasMatch(stamp),
      'Invalid publication time');
  final parsed = DateTime.tryParse(stamp as String);
  _need(parsed != null && parsed.toUtc().toIso8601String().replaceFirst('.000Z', 'Z') == stamp,
      'Invalid calendar time');
  final commits = _object(manifest['sourceCommits']);
  _need(commits.length == 2 && commits.containsKey('Mehael-Yeh/Rstone') &&
      commits.containsKey('Mehael-Yeh/RstoneTDS') && commits.values.every((v) =>
          v is String && RegExp(r'^[0-9a-f]{40}$').hasMatch(v)), 'Invalid source commits');
  _need(manifest['files'] is List, 'Missing full file inventory');
  final records = manifest['files'] as List;
  _need(records.isNotEmpty && records.length <= 100000, 'Empty or oversized inventory');
  final files = <DataFile>[];
  final paths = <String>{}, sizes = <String, int>{};
  String? previous;
  var total = 0;
  for (final value in records) {
    final record = _object(value);
    _need(record['path'] is String, 'Invalid path');
    final path = record['path'] as String;
    validateLogicalPath(path);
    _need(paths.add(protocolCaseFold(path)), 'Duplicate or case-colliding path');
    _need(previous == null || _utf8Compare(previous, path) < 0, 'Unsorted inventory');
    previous = path;
    _need(record['sha256'] is String && _hashPattern.hasMatch(record['sha256']), 'Invalid object hash');
    _need(_integer(record['size'], 1) && record['size'] <= 16 * 1024 * 1024, 'Invalid object size');
    _need(record['contentType'] == null || record['contentType'] == 'text/markdown; charset=utf-8',
        'Invalid content type');
    final hash = record['sha256'] as String, size = record['size'] as int;
    _need(!sizes.containsKey(hash) || sizes[hash] == size, 'Conflicting object sizes');
    sizes[hash] = size;
    total += size;
    files.add(DataFile(path, hash, size));
  }
  _need(total <= 512 * 1024 * 1024, 'Dataset too large');
  _need(sequence >= highestSequence, 'Stale sequence');
  if (sequence == highestSequence) {
    _need(digest == acceptedManifestSha256, 'Same-sequence manifest conflict');
  }
  return VerifiedManifest._(sequence, manifest['dataVersion'] as String, digest,
      List.unmodifiable(files));
}

Future<bool> verifyObject(DataFile file, Uint8List raw) async =>
    raw.length == file.size && await sha256Hex(raw) == file.sha256;

class SyncPlan {
  final List<String> added, modified, deleted, unchanged;
  final List<DataFile> missingObjects;
  SyncPlan(this.added, this.modified, this.deleted, this.unchanged, this.missingObjects);
}

/// Cache hashes MUST have been verified against local raw bytes, not inferred from filenames.
SyncPlan planSync(VerifiedManifest? old, VerifiedManifest target, Set<String> verifiedCacheHashes) {
  final previous = {for (final f in old?.files ?? <DataFile>[]) f.path: f.sha256};
  final next = {for (final f in target.files) f.path: f.sha256};
  final missing = <String, DataFile>{};
  for (final file in target.files) {
    if (!verifiedCacheHashes.contains(file.sha256)) missing[file.sha256] = file;
  }
  return SyncPlan(
      next.keys.where((p) => !previous.containsKey(p)).toList()..sort(),
      next.keys.where((p) => previous.containsKey(p) && previous[p] != next[p]).toList()..sort(),
      previous.keys.where((p) => !next.containsKey(p)).toList()..sort(),
      next.keys.where((p) => previous[p] == next[p]).toList()..sort(), missing.values.toList());
}

/// dart:convert alone keeps the last duplicate key. Guard keys and depth before jsonDecode.
dynamic parseUniqueJson(Uint8List bytes) {
  final text = utf8.decode(bytes);
  final guard = _JsonGuard(text);
  guard.value(0);
  guard.whitespace();
  _need(guard.position == text.length, 'Trailing JSON content');
  return jsonDecode(text);
}

class _JsonGuard {
  final String text;
  int position = 0;
  _JsonGuard(this.text);
  void whitespace() {
    while (position < text.length && ' \t\r\n'.contains(text[position])) position++;
  }
  void expect(String character) {
    whitespace();
    _need(position < text.length && text[position] == character, 'Invalid JSON delimiter');
    position++;
  }
  String string() {
    whitespace();
    final start = position;
    expect('"');
    while (position < text.length) {
      final character = text[position++];
      if (character == '\\') {
        position++;
      } else if (character == '"') {
        return jsonDecode(text.substring(start, position)) as String;
      }
    }
    throw const FormatException('Unterminated JSON string');
  }
  void value(int depth) {
    _need(depth <= 64, 'JSON nesting limit');
    whitespace();
    _need(position < text.length, 'Truncated JSON');
    final character = text[position];
    if (character == '{') {
      position++;
      whitespace();
      final keys = <String>{};
      if (position < text.length && text[position] == '}') { position++; return; }
      while (true) {
        _need(keys.add(string()), 'Duplicate JSON key');
        expect(':');
        value(depth + 1);
        whitespace();
        if (position < text.length && text[position] == '}') { position++; return; }
        expect(',');
      }
    } else if (character == '[') {
      position++;
      whitespace();
      if (position < text.length && text[position] == ']') { position++; return; }
      while (true) {
        value(depth + 1);
        whitespace();
        if (position < text.length && text[position] == ']') { position++; return; }
        expect(',');
      }
    } else if (character == '"') {
      string();
    } else {
      final start = position;
      while (position < text.length && !' \t\r\n,]}'.contains(text[position])) position++;
      _need(position > start, 'Invalid JSON value');
    }
  }
}
