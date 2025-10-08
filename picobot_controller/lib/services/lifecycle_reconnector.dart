import 'package:flutter/widgets.dart';
import 'package:provider/provider.dart';
import '../providers/connection_provider.dart';
import 'logger_service.dart';

class LifecycleReconnector extends StatefulWidget {
  final Widget child;
  const LifecycleReconnector({super.key, required this.child});

  @override
  State<LifecycleReconnector> createState() => _LifecycleReconnectorState();
}

class _LifecycleReconnectorState extends State<LifecycleReconnector>
    with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final provider = context.read<ConnectionProvider>();
    switch (state) {
      case AppLifecycleState.resumed:
        LoggerService().i('Lifecycle', 'App resumed; attempting clean WS reconnect');
        // Perform a clean disconnect first to reset any stuck state
        try { provider.disconnect(); } catch (_) {}
        Future.delayed(const Duration(milliseconds: 200), () async {
          if (!mounted) return;
          if (provider.selectedProfile != null) {
            await provider.connect();
            if (provider.isConnected) {
              provider.requestPlaylists();
            }
          }
        });
        break;
      case AppLifecycleState.inactive:
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
      case AppLifecycleState.hidden:
        LoggerService().i('Lifecycle', 'App backgrounding; closing WS');
        try { provider.disconnect(); } catch (_) {}
        break;
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
