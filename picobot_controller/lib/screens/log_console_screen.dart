import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../services/logger_service.dart';

class LogConsoleScreen extends StatefulWidget {
  const LogConsoleScreen({super.key});

  @override
  State<LogConsoleScreen> createState() => _LogConsoleScreenState();
}

class _LogConsoleScreenState extends State<LogConsoleScreen> {
  final _scrollController = ScrollController();
  bool _autoScroll = true;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(() {
      // If the user scrolls up, disable autoscroll until they reach bottom.
      final atBottom = _scrollController.position.pixels >=
          _scrollController.position.maxScrollExtent - 24;
      setState(() => _autoScroll = atBottom);
    });
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final logger = LoggerService();
    final levels = LogLevel.values;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Logs'),
        actions: [
          // Level filter (icon-like InkWell with popup menu)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4.0),
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: kMinInteractiveDimension),
              child: PopupMenuButton<LogLevel>(
                tooltip: 'Log level',
                onSelected: (lv) => setState(() => logger.minLevel = lv),
                itemBuilder: (context) => levels
                    .map((lv) => PopupMenuItem<LogLevel>(
                          value: lv,
                          child: Text(lv.name.toUpperCase()),
                        ))
                    .toList(),
                child: Container(
                  alignment: Alignment.center,
                  constraints: const BoxConstraints(minHeight: kMinInteractiveDimension),
                  padding: const EdgeInsets.symmetric(horizontal: 12.0),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.tune, size: 20),
                      const SizedBox(width: 6),
                      Text(logger.minLevel.name.toUpperCase()),
                    ],
                  ),
                ),
              ),
            ),
          ),
          IconButton(
            tooltip: 'Copy',
            icon: const Icon(Icons.copy_all),
            onPressed: () async {
              final txt = logger.dumpAsText();
              await Clipboard.setData(ClipboardData(text: txt));
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Logs copied to clipboard')),
                );
              }
            },
          ),
          IconButton(
            tooltip: 'Clear',
            icon: const Icon(Icons.delete_sweep_outlined),
            onPressed: () => setState(() => logger.clear()),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: StreamBuilder<LogEntry>(
        stream: logger.stream,
        builder: (context, snapshot) {
          final entries = logger.buffer;
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (_autoScroll && _scrollController.hasClients) {
              _scrollController.jumpTo(
                _scrollController.position.maxScrollExtent,
              );
            }
          });
          return ListView.builder(
            controller: _scrollController,
            itemCount: entries.length,
            itemBuilder: (context, i) {
              final e = entries[i];
              final color = switch (e.level) {
                LogLevel.verbose => Colors.grey,
                LogLevel.debug => Colors.blueGrey,
                LogLevel.info => Colors.white,
                LogLevel.warn => Colors.amber,
                LogLevel.error => Colors.redAccent,
              };
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                child: DefaultTextStyle(
                  style: TextStyle(color: color),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('[${e.ts.toIso8601String()}] [${e.level.name.toUpperCase()}] [${e.tag}]'),
                      Text(e.message),
                      if (e.error != null) Text('error: ${e.error}'),
                      if (e.stack != null) Text(e.stack.toString()),
                    ],
                  ),
                ),
              );
            },
          );
        },
      ),
      bottomNavigationBar: Padding(
        padding: const EdgeInsets.all(8),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            Icon(
              _autoScroll ? Icons.arrow_downward : Icons.arrow_downward_outlined,
              size: 16,
            ),
            const SizedBox(width: 6),
            const Text('Auto-scroll'),
            Switch(
              value: _autoScroll,
              onChanged: (v) => setState(() => _autoScroll = v),
            )
          ],
        ),
      ),
    );
  }
}
