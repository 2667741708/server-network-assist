import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import { CodexSession, Host, ProbeResult } from './models';

@Component({
  selector: 'app-codex-chat',
  imports: [FormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  template: `
    <header class="page-head">
      <div>
        <p class="eyebrow">REMOTE CODEX</p>
        <h1>服务器 Codex 对话</h1>
        <p>消息通过已锁定指纹的 SSH 通道交给远端 Codex CLI；回复和会话记录返回当前管理台。</p>
      </div>
      <button mat-stroked-button (click)="loadSessions()">刷新会话</button>
    </header>
    @if (message()) { <div class="alert" [class.error]="failed()">{{ message() }}</div> }
    <section class="codex-shell">
      <aside class="codex-session-list">
        <mat-form-field appearance="outline">
          <mat-label>服务器</mat-label>
          <mat-select [(ngModel)]="hostId" (selectionChange)="hostChanged()">
            @for (host of codexHosts; track host.id) {
              <mat-option [value]="host.id">{{ host.name }} · {{ status(host.id) }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
        <button mat-flat-button [disabled]="!hostId" (click)="newSession()">新对话</button>
        <div class="session-scroll">
          @for (session of filteredSessions; track session.id) {
            <button class="session-item" [class.active]="selected()?.id === session.id" (click)="open(session)">
              <strong>{{ session.title }}</strong>
              <span>{{ session.status === 'running' ? '正在回复' : formatTime(session.updated_at) }}</span>
            </button>
          } @empty {
            <p class="empty-state">这台服务器还没有对话。</p>
          }
        </div>
      </aside>
      <mat-card class="codex-conversation">
        @if (selected(); as session) {
          <div class="conversation-head">
            <div>
              <strong>{{ session.title }}</strong>
              <span class="mono long-value">{{ hostName(session.host_id) }} · {{ session.workspace || '远端主目录' }}</span>
            </div>
            <div class="button-row">
              @if (session.status === 'running') {
                <button mat-stroked-button class="danger" (click)="cancel()">停止</button>
              }
              <button mat-button class="danger" [disabled]="session.status === 'running'" (click)="remove()">删除</button>
            </div>
          </div>
          <div class="message-scroll" aria-live="polite">
            @for (item of session.messages || []; track item.id) {
              <article class="chat-message" [class.user]="item.role === 'user'" [class.error]="item.status === 'error'">
                <span>{{ item.role === 'user' ? '你' : 'Codex' }}</span>
                @if (item.status === 'running') { <p class="thinking">正在远端服务器上思考与执行…</p> }
                @else { <pre>{{ item.content }}</pre> }
              </article>
            } @empty {
              <div class="conversation-empty">
                <strong>开始与 {{ hostName(session.host_id) }} 上的 Codex 对话</strong>
                <p>默认使用 {{ session.sandbox === 'read-only' ? '只读' : '工作区可写' }} 沙箱。</p>
              </div>
            }
          </div>
          <div class="composer">
            <textarea [(ngModel)]="prompt" rows="3" maxlength="32000" placeholder="给这台服务器上的 Codex 发送消息…" (keydown.control.enter)="send()"></textarea>
            <button mat-flat-button [disabled]="!prompt.trim() || session.status === 'running'" (click)="send()">发送</button>
          </div>
        } @else {
          <div class="conversation-empty"><strong>选择服务器，然后开始新对话</strong><p>只有成功安装并登录 Codex CLI 的服务器才能回复。</p></div>
        }
      </mat-card>
    </section>
  `,
})
export class CodexChatComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) hosts: Host[] = [];
  @Input({ required: true }) probes: ProbeResult[] = [];
  @Input() requestedHost = '';
  @Output() requestedHostChange = new EventEmitter<string>();
  readonly sessions = signal<CodexSession[]>([]);
  readonly selected = signal<CodexSession | null>(null);
  readonly message = signal('');
  readonly failed = signal(false);
  hostId = '';
  prompt = '';
  private poll?: ReturnType<typeof setInterval>;

  constructor(private readonly api: ApiService) {
    this.poll = setInterval(() => {
      if (this.selected()?.status === 'running') this.refreshSelected();
    }, 1800);
  }

  ngOnChanges(changes: SimpleChanges) {
    if (changes['requestedHost'] && this.requestedHost) {
      this.hostId = this.requestedHost;
      this.requestedHostChange.emit('');
    } else if (!this.hostId && this.codexHosts.length) {
      this.hostId = this.codexHosts[0].id;
    }
    this.loadSessions();
  }

  get codexHosts() { return this.hosts.filter((host) => host.codex_enabled !== false); }
  get filteredSessions() { return this.sessions().filter((session) => session.host_id === this.hostId); }
  status(id: string) {
    const probe = this.probes.find((value) => value.id === id);
    if (!probe) return '等待探测';
    if (!probe.ssh) return 'SSH 不可达';
    return probe.codex ? (probe.codex_version || 'Codex 可用') : '未检测到 Codex';
  }
  hostName(id: string) { return this.hosts.find((host) => host.id === id)?.name || id; }
  formatTime(value: number) { return new Date(value * 1000).toLocaleString(); }
  notify(value: string, failed = false) { this.message.set(value); this.failed.set(failed); }

  loadSessions() {
    this.api.get<{ sessions: CodexSession[] }>('/api/codex/sessions').subscribe({
      next: (value) => {
        this.sessions.set(value.sessions);
        const selected = this.selected();
        if (selected) {
          const summary = value.sessions.find((item) => item.id === selected.id);
          if (summary) this.refreshSelected();
          else this.selected.set(null);
        }
      },
      error: (e) => this.notify(e.message, true),
    });
  }
  hostChanged() { this.selected.set(null); }
  newSession() {
    const host = this.hosts.find((value) => value.id === this.hostId);
    if (!host) return;
    this.api.post<{ session: CodexSession }>('/api/codex/session/create', {
      host_id: host.id,
      title: `${host.name} 对话`,
      workspace: host.codex_workspace || '',
      sandbox: 'workspace-write',
    }).subscribe({
      next: (value) => { this.selected.set(value.session); this.loadSessions(); this.notify('新对话已创建'); },
      error: (e) => this.notify(e.message, true),
    });
  }
  open(session: CodexSession) {
    this.hostId = session.host_id;
    this.api.get<{ session: CodexSession }>(`/api/codex/session?id=${encodeURIComponent(session.id)}`).subscribe({
      next: (value) => this.selected.set(value.session),
      error: (e) => this.notify(e.message, true),
    });
  }
  refreshSelected() { const value = this.selected(); if (value) this.open(value); }
  send() {
    const value = this.selected();
    const prompt = this.prompt.trim();
    if (!value || !prompt || value.status === 'running') return;
    this.prompt = '';
    this.api.post<{ session: CodexSession }>('/api/codex/message', { id: value.id, prompt }).subscribe({
      next: (result) => { this.selected.set(result.session); this.loadSessions(); },
      error: (e) => { this.prompt = prompt; this.notify(e.message, true); },
    });
  }
  cancel() {
    const value = this.selected(); if (!value) return;
    this.api.post('/api/codex/cancel', { id: value.id }).subscribe({
      next: () => setTimeout(() => this.refreshSelected(), 250), error: (e) => this.notify(e.message, true),
    });
  }
  remove() {
    const value = this.selected(); if (!value || !confirm(`删除“${value.title}”及全部消息吗？`)) return;
    this.api.post('/api/codex/session/delete', { id: value.id }).subscribe({
      next: () => { this.selected.set(null); this.loadSessions(); }, error: (e) => this.notify(e.message, true),
    });
  }
  ngOnDestroy() { if (this.poll) clearInterval(this.poll); }
}
