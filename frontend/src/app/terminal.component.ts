import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  OnDestroy,
  ViewChild,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatSelectModule } from '@angular/material/select';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import { ApiService } from './api.service';
import { Host } from './models';

@Component({
  selector: 'app-terminal',
  imports: [FormsModule, MatButtonModule, MatCardModule, MatCheckboxModule, MatSelectModule],
  template: `<header class="page-head">
      <div>
        <p class="eyebrow">WEB TERMINAL</p>
        <h1>SSH 终端</h1>
        <p>浏览器只持有一次性票据；SSH 凭据不会发送到前端。</p>
      </div>
    </header>
    <mat-card class="terminal-card"
      ><div class="terminal-toolbar">
        <mat-select aria-label="选择主机" [(ngModel)]="hostId"
          ><mat-option value="">选择主机</mat-option>
          @for (host of terminalHosts; track host.id) {
            <mat-option [value]="host.id">{{ host.name }} · {{ host.address }}</mat-option>
          }</mat-select
        ><mat-checkbox [(ngModel)]="persistent">使用 tmux 持久会话</mat-checkbox
        ><button mat-flat-button (click)="connect()" [disabled]="!hostId || connected()">
          连接</button
        ><button mat-stroked-button (click)="disconnect()" [disabled]="!connected()">断开</button>
      </div>
      <div class="terminal-status">{{ status() }}</div>
      <div #terminal class="terminal-view" aria-label="SSH 终端"></div
    ></mat-card>`,
})
export class TerminalComponent implements AfterViewInit, OnDestroy {
  @Input({ required: true }) hosts: Host[] = [];
  @ViewChild('terminal', { static: true }) terminalElement!: ElementRef<HTMLDivElement>;
  readonly connected = signal(false);
  readonly status = signal('请选择主机并连接');
  hostId = '';
  persistent = false;
  private terminal?: Terminal;
  private fit?: FitAddon;
  private socket?: WebSocket;
  private observer?: ResizeObserver;
  constructor(private readonly api: ApiService) {}
  get terminalHosts() {
    return this.hosts.filter((value) => value.terminal_enabled);
  }
  ngAfterViewInit() {
    this.terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
      fontSize: 14,
      theme: { background: '#07111f', foreground: '#d9e7f5', cursor: '#64d8cb' },
    });
    this.fit = new FitAddon();
    this.terminal.loadAddon(this.fit);
    this.terminal.open(this.terminalElement.nativeElement);
    this.fit.fit();
    this.terminal.writeln('\x1b[1;36mServer Network Assist\x1b[0m');
    this.terminal.writeln('等待连接…');
    this.terminal.onData((data) => {
      if (this.socket?.readyState === WebSocket.OPEN)
        this.socket.send(JSON.stringify({ type: 'input', data }));
    });
    this.observer = new ResizeObserver(() => {
      this.fit?.fit();
      if (this.socket?.readyState === WebSocket.OPEN)
        this.socket.send(
          JSON.stringify({ type: 'resize', cols: this.terminal?.cols, rows: this.terminal?.rows }),
        );
    });
    this.observer.observe(this.terminalElement.nativeElement);
  }
  connect() {
    this.status.set('正在申请一次性连接票据…');
    this.api
      .post<{ ticket: string }>('/api/terminal/ticket', {
        id: this.hostId,
        persistent: this.persistent,
      })
      .subscribe({
        next: (value) => this.open(value.ticket),
        error: (e) => this.status.set(e.message),
      });
  }
  open(ticket: string) {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    this.socket = new WebSocket(`${scheme}://${location.host}/ws`);
    this.socket.binaryType = 'arraybuffer';
    this.socket.onopen = () => this.socket?.send(JSON.stringify({ ticket }));
    this.socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.terminal?.write(new Uint8Array(event.data));
        return;
      }
      try {
        const value = JSON.parse(event.data);
        if (value.type === 'ready') {
          this.connected.set(true);
          this.status.set(`已连接：${value.route.join(' → ')}`);
          this.terminal?.clear();
          this.socket?.send(
            JSON.stringify({
              type: 'resize',
              cols: this.terminal?.cols,
              rows: this.terminal?.rows,
            }),
          );
        } else if (value.type === 'error') {
          this.status.set(value.message);
        }
      } catch {
        this.terminal?.write(String(event.data));
      }
    };
    this.socket.onclose = () => {
      this.connected.set(false);
      this.status.update((v) => (v.startsWith('已连接') ? '连接已关闭' : v));
    };
    this.socket.onerror = () => this.status.set('WebSocket 连接失败');
  }
  disconnect() {
    this.socket?.close(1000, 'user');
    this.socket = undefined;
    this.connected.set(false);
    this.status.set('已断开');
  }
  ngOnDestroy() {
    this.disconnect();
    this.observer?.disconnect();
    this.terminal?.dispose();
  }
}
