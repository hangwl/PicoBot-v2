import 'dart:async';
import 'package:flutter/foundation.dart';

enum LogLevel { verbose, debug, info, warn, error }

class LogEntry {
  final DateTime ts;
  final LogLevel level;
  final String tag;
  final String message;
  final Object? error;
  final StackTrace? stack;
  LogEntry(this.level, this.tag, this.message, {this.error, this.stack})
      : ts = DateTime.now();
}

class LoggerService {
  static final LoggerService _instance = LoggerService._internal();
  factory LoggerService() => _instance;
  LoggerService._internal();

  final _controller = StreamController<LogEntry>.broadcast();
  final List<LogEntry> _buffer = <LogEntry>[];

  // Configurable knobs
  int bufferLimit = 800;
  LogLevel minLevel = LogLevel.debug;
  bool enableConsole = !kReleaseMode;
  Set<String>? tagFilter; // null = all tags

  Stream<LogEntry> get stream => _controller.stream;
  List<LogEntry> get buffer => List.unmodifiable(_buffer);

  void clear() {
    _buffer.clear();
    // Emit a system info to signal UI refresh (optional)
    _emit(LogEntry(LogLevel.info, 'Logger', 'Log cleared'));
  }

  String dumpAsText() {
    final sb = StringBuffer();
    for (final e in _buffer) {
      final ts = e.ts.toIso8601String();
      final lvl = e.level.name.toUpperCase();
      sb.writeln('[$ts][$lvl][${e.tag}] ${e.message}');
      if (e.error != null) sb.writeln('  error: ${e.error}');
      if (e.stack != null) sb.writeln(e.stack);
    }
    return sb.toString();
  }

  void _emit(LogEntry e) {
    if (e.level.index < minLevel.index) return;
    if (tagFilter != null && !tagFilter!.contains(e.tag)) return;
    if (_buffer.length >= bufferLimit) _buffer.removeAt(0);
    _buffer.add(e);
    _controller.add(e);

    if (enableConsole) {
      final prefix =
          '[${e.ts.toIso8601String()}][${e.level.name.toUpperCase()}][${e.tag}]';
      if (e.error != null) {
        debugPrint('$prefix ${e.message} | error=${e.error}\n${e.stack ?? ''}');
      } else {
        debugPrint('$prefix ${e.message}');
      }
    }
  }

  // Eager message APIs
  void v(String tag, String msg) => _emit(LogEntry(LogLevel.verbose, tag, msg));
  void d(String tag, String msg) => _emit(LogEntry(LogLevel.debug, tag, msg));
  void i(String tag, String msg) => _emit(LogEntry(LogLevel.info, tag, msg));
  void w(String tag, String msg) => _emit(LogEntry(LogLevel.warn, tag, msg));
  void e(String tag, String msg, [Object? err, StackTrace? st]) =>
      _emit(LogEntry(LogLevel.error, tag, msg, error: err, stack: st));

  // Lazy message APIs (avoid building strings unless emitted)
  void vF(String tag, String Function() msg) {
    if (LogLevel.verbose.index >= minLevel.index) v(tag, msg());
  }

  void dF(String tag, String Function() msg) {
    if (LogLevel.debug.index >= minLevel.index) d(tag, msg());
  }

  void iF(String tag, String Function() msg) {
    if (LogLevel.info.index >= minLevel.index) i(tag, msg());
  }

  void wF(String tag, String Function() msg) {
    if (LogLevel.warn.index >= minLevel.index) w(tag, msg());
  }

  void eF(String tag, String Function() msg, [Object? err, StackTrace? st]) {
    if (LogLevel.error.index >= minLevel.index) e(tag, msg(), err, st);
  }
}
