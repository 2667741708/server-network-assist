import { Component, Input, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import { Host, ProbeResult } from './models';

@Component({
  selector: 'app-browser',
  imports: [FormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  template: `
    <header class="page-head"><div><p class="eyebrow">REMOTE BROWSER</p><h1>服务器浏览器标签</h1>
      <p>浏览器运行在所选服务器；画面和键盘鼠标通过已锁定指纹的 SSH 通道转发，配置与登录状态保存在服务器。</p></div></header>
    @if (message()) { <div class="alert" [class.error]="failed()">{{ message() }}</div> }
    <mat-card class="browser-card">
      <div class="browser-toolbar">
        <mat-form-field appearance="outline"><mat-label>服务器</mat-label><mat-select [(ngModel)]="hostId">
          @for (host of browserHosts; track host.id) { <mat-option [value]="host.id">{{ host.name }} · {{ browserStatus(host.id) }}</mat-option> }
        </mat-select></mat-form-field>
        <mat-form-field appearance="outline" class="browser-address"><mat-label>地址</mat-label>
          <input matInput [(ngModel)]="url" (keydown.enter)="navigate()"></mat-form-field>
        <button mat-flat-button [disabled]="!hostId || connecting()" (click)="connect()">打开</button>
        <button mat-stroked-button [disabled]="!connected()" (click)="disconnect()">关闭</button>
      </div>
      <div class="browser-screen" tabindex="0" (keydown)="key($event)">
        @if (frame()) {
          <img [src]="'data:image/jpeg;base64,' + frame()" alt="服务器浏览器实时画面" draggable="false" (click)="clickFrame($event)">
        } @else {
          <div class="conversation-empty"><strong>选择服务器并打开 ChatGPT</strong>
            <p>首次使用需要在这个远端浏览器中登录；以后会复用该服务器专用配置。</p></div>
        }
      </div>
      <div class="browser-input">
        <mat-form-field appearance="outline"><mat-label>向当前输入框键入（本地隐藏）</mat-label>
          <input matInput type="password" [(ngModel)]="typed" autocomplete="off" (keydown.enter)="sendText()"></mat-form-field>
        <button mat-stroked-button [disabled]="!connected() || !typed" (click)="sendText()">发送并清空</button>
        <span>点击画面定位；可使用 Enter、Tab、Backspace、方向键。</span>
      </div>
    </mat-card>
  `,
})
export class BrowserComponent implements OnDestroy {
  @Input({required: true}) hosts: Host[] = [];
  @Input({required: true}) probes: ProbeResult[] = [];
  readonly frame = signal('');
  readonly connected = signal(false);
  readonly connecting = signal(false);
  readonly message = signal('');
  readonly failed = signal(false);
  hostId = ''; url = 'https://chatgpt.com/'; typed = '';
  private socket?: WebSocket;
  private width = 1280; private height = 800;
  constructor(private readonly api: ApiService) {}
  get browserHosts() {
    const values = this.hosts.filter(host => host.browser_enabled !== false);
    if (!this.hostId && values.length) this.hostId = values[0].id;
    return values;
  }
  browserStatus(id: string) {
    const probe = this.probes.find(value => value.id === id);
    if (!probe) return '等待探测';
    return probe.browser ? (probe.browser_name || '浏览器可用') : '未检测到 Chrome/Edge';
  }
  connect() {
    if (!this.hostId || this.connecting()) return;
    this.disconnect(); this.connecting.set(true); this.failed.set(false); this.message.set('正在启动服务器浏览器…');
    this.api.post<{ticket: string}>('/api/browser/ticket', {id: this.hostId, url: this.url}).subscribe({
      next: value => this.openSocket(value.ticket),
      error: error => { this.connecting.set(false); this.failed.set(true); this.message.set(error.message); },
    });
  }
  openSocket(ticket: string) {
    const base = new URL(document.baseURI);
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.socket = new WebSocket(`${scheme}//${location.host}${base.pathname.replace(/\/$/, '')}/ws/browser`);
    this.socket.onopen = () => this.socket?.send(JSON.stringify({ticket}));
    this.socket.onmessage = event => {
      const value = JSON.parse(String(event.data));
      if (value.type === 'ready') {
        this.width = value.width || 1280; this.height = value.height || 800;
        this.connecting.set(false); this.connected.set(true); this.message.set('服务器浏览器已连接');
      } else if (value.type === 'frame') this.frame.set(value.data);
      else if (value.type === 'location') this.url = value.url || this.url;
      else if (value.type === 'error') { this.failed.set(true); this.message.set(value.message); }
    };
    this.socket.onerror = () => { this.failed.set(true); this.message.set('浏览器通道连接失败'); };
    this.socket.onclose = () => { this.connected.set(false); this.connecting.set(false); };
  }
  send(value: Record<string, unknown>) { if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(value)); }
  navigate() { if (this.connected()) this.send({type: 'navigate', url: this.url}); else this.connect(); }
  clickFrame(event: MouseEvent) {
    const image = event.currentTarget as HTMLImageElement;
    const rect = image.getBoundingClientRect();
    this.send({type: 'click', x: (event.clientX - rect.left) * this.width / rect.width,
      y: (event.clientY - rect.top) * this.height / rect.height});
    image.parentElement?.focus();
  }
  sendText() { if (!this.typed) return; this.send({type: 'text', text: this.typed}); this.typed = ''; }
  key(event: KeyboardEvent) {
    if (!this.connected() || event.ctrlKey || event.altKey || event.metaKey) return;
    const allowed = ['Enter', 'Tab', 'Backspace', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete'];
    if (allowed.includes(event.key)) { event.preventDefault(); this.send({type: 'key', key: event.key}); }
  }
  disconnect() { this.socket?.close(); this.socket = undefined; this.connected.set(false); this.connecting.set(false); this.frame.set(''); }
  ngOnDestroy() { this.disconnect(); }
}
