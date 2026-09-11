import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import { CodexModel, CodexProject, CodexRemoteThread, CodexSession, Host, ProbeResult } from './models';

@Component({
  selector: 'app-codex-chat',
  imports: [FormsModule, MatButtonModule, MatCardModule, MatCheckboxModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  template: `
    <header class="page-head"><div><p class="eyebrow">REMOTE CODEX WORKSPACE</p><h1>服务器 Codex 工作台</h1>
      <p>读取服务器上的真实 Codex 会话和项目，并通过 SSH 接着使用。</p></div>
      <button mat-stroked-button (click)="refreshAll()">刷新服务器数据</button></header>
    @if (message()) { <div class="alert" [class.error]="failed()">{{ message() }}</div> }
    <section class="codex-shell">
      <aside class="codex-session-list">
        <mat-form-field appearance="outline"><mat-label>服务器</mat-label><mat-select [(ngModel)]="hostId" (selectionChange)="hostChanged()">
          @for (host of codexHosts; track host.id) { <mat-option [value]="host.id">{{ host.name }} · {{ status(host.id) }}</mat-option> }
        </mat-select></mat-form-field>
        <div class="button-row"><button mat-flat-button [disabled]="!hostId" (click)="newSession()">新对话</button>
          <button mat-button [disabled]="!hostId" (click)="plainChat()">普通聊天</button>
          <button mat-button (click)="view='history'">真实历史</button><button mat-button (click)="view='projects'">项目</button></div>
        <div class="session-scroll">
          @if (view === 'sessions') {
            @for (session of filteredSessions; track session.id) {
              <button class="session-item" [class.active]="selected()?.id === session.id" (click)="open(session)">
                <strong>{{ session.title }}</strong><span>{{ session.model || '默认模型' }} · {{ formatTime(session.updated_at) }}</span></button>
            } @empty { <p class="empty-state">没有面板对话，可从真实历史继续。</p> }
          } @else if (view === 'history') {
            @for (thread of threads(); track thread.id) {
              <button class="session-item" [class.active]="remoteSelected()?.id === thread.id" (click)="openRemote(thread)">
                <strong>{{ thread.name || thread.preview || '未命名会话' }}</strong>
                <span>{{ thread.model || '未知模型' }} · {{ thread.reasoningEffort || '默认推理' }}</span>
                <span class="mono long-value">{{ thread.cwd || '远端主目录' }}</span></button>
            } @empty { <p class="empty-state">尚未读取到真实会话。</p> }
          } @else {
            @for (project of projects(); track project.path) {
              <button class="session-item" (click)="chooseProject(project)"><strong class="mono long-value">{{ project.path }}</strong>
                <span>{{ project.count }} 个会话 · {{ formatTime(project.updated_at) }}</span></button>
            } @empty { <p class="empty-state">会话中尚无项目目录。</p> }
          }
        </div>
      </aside>
      <mat-card class="codex-conversation">
        @if (remoteSelected(); as thread) {
          <div class="conversation-head"><div><strong>{{ thread.name || '服务器历史会话' }}</strong>
            <span class="mono long-value">{{ thread.cwd || '远端主目录' }}</span></div>
            <button mat-flat-button (click)="continueRemote(thread)">继续此会话</button></div>
          <div class="message-scroll">
            @for (item of remoteMessages(); track $index) {
              <article class="chat-message" [class.user]="item.role === 'user'"><span>{{ item.role === 'user' ? '你' : 'Codex' }}</span><pre>{{ item.content }}</pre></article>
            } @empty { <div class="conversation-empty"><strong>该会话没有可显示的文本消息</strong></div> }
          </div>
        } @else if (selected(); as session) {
          <div class="conversation-head"><div><strong>{{ session.title }}</strong>
            <span class="mono long-value">{{ hostName(session.host_id) }} · {{ session.workspace || '远端主目录' }}</span>
            <span>{{ session.model || '默认模型' }} · {{ session.reasoning_effort || '默认推理' }} · {{ session.service_tier === 'priority' ? 'Fast' : '标准速度' }}</span></div>
            <div class="button-row">@if (session.status === 'running') { <button mat-stroked-button class="danger" (click)="cancel()">停止</button> }
              <button mat-button class="danger" [disabled]="session.status === 'running'" (click)="remove()">删除</button></div></div>
          <div class="message-scroll" aria-live="polite">
            @for (item of session.messages || []; track item.id) {
              <article class="chat-message" [class.user]="item.role === 'user'" [class.error]="item.status === 'error'">
                <span>{{ item.role === 'user' ? '你' : 'Codex' }}</span>
                @if (item.status === 'running') { <p class="thinking">正在远端服务器上思考与执行…</p> } @else { <pre>{{ item.content }}</pre> }
              </article>
            } @empty { <div class="conversation-empty"><strong>开始对话</strong><p>模型与推理设置会沿用到后续回复。</p></div> }
          </div>
          <div class="composer"><textarea [(ngModel)]="prompt" rows="3" maxlength="32000" placeholder="给服务器上的 Codex 发送消息…" (keydown.control.enter)="send()"></textarea>
            <button mat-flat-button [disabled]="!prompt.trim() || session.status === 'running'" (click)="send()">发送</button></div>
        } @else {
          <div class="conversation-empty new-codex"><strong>新对话设置</strong>
            <mat-form-field appearance="outline"><mat-label>项目目录</mat-label><input matInput [(ngModel)]="workspace" placeholder="留空使用远端主目录"></mat-form-field>
            <mat-form-field appearance="outline"><mat-label>模型</mat-label><mat-select [(ngModel)]="modelId" (selectionChange)="modelChanged()">
              @for (model of models(); track model.id) { <mat-option [value]="model.id">{{ model.displayName }}</mat-option> }</mat-select></mat-form-field>
            <mat-form-field appearance="outline"><mat-label>推理强度</mat-label><mat-select [(ngModel)]="effort">
              @for (value of efforts; track value.reasoningEffort) { <mat-option [value]="value.reasoningEffort">{{ value.reasoningEffort }} · {{ value.description }}</mat-option> }</mat-select></mat-form-field>
            <mat-form-field appearance="outline"><mat-label>权限</mat-label><mat-select [(ngModel)]="sandbox">
              <mat-option value="read-only">只读</mat-option><mat-option value="workspace-write">工作区可写</mat-option></mat-select></mat-form-field>
            <mat-checkbox [(ngModel)]="fast" [disabled]="!fastSupported">Fast（更快，会增加用量）</mat-checkbox>
            <button mat-flat-button [disabled]="!hostId" (click)="createConfigured()">创建对话</button>
          </div>
        }
      </mat-card>
    </section>`,
})
export class CodexChatComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) hosts: Host[] = [];
  @Input({ required: true }) probes: ProbeResult[] = [];
  @Input() requestedHost = '';
  @Output() requestedHostChange = new EventEmitter<string>();
  readonly sessions = signal<CodexSession[]>([]);
  readonly selected = signal<CodexSession | null>(null);
  readonly threads = signal<CodexRemoteThread[]>([]);
  readonly projects = signal<CodexProject[]>([]);
  readonly models = signal<CodexModel[]>([]);
  readonly remoteSelected = signal<CodexRemoteThread | null>(null);
  readonly remoteMessages = signal<Array<{role: string; content: string}>>([]);
  readonly message = signal('');
  readonly failed = signal(false);
  hostId = ''; prompt = ''; view: 'sessions'|'history'|'projects' = 'history';
  workspace = ''; modelId = ''; effort = ''; sandbox = 'workspace-write'; fast = false;
  private poll?: ReturnType<typeof setInterval>;

  constructor(private readonly api: ApiService) {
    this.poll = setInterval(() => { if (this.selected()?.status === 'running') this.refreshSelected(); }, 1800);
  }
  ngOnChanges(changes: SimpleChanges) {
    if (changes['requestedHost'] && this.requestedHost) { this.hostId = this.requestedHost; this.requestedHostChange.emit(''); }
    else if (!this.hostId && this.codexHosts.length) this.hostId = this.codexHosts[0].id;
    this.refreshAll();
  }
  get codexHosts() { return this.hosts.filter(h => h.codex_enabled !== false); }
  get filteredSessions() { return this.sessions().filter(s => s.host_id === this.hostId); }
  get selectedModel() { return this.models().find(m => m.id === this.modelId); }
  get efforts() { return this.selectedModel?.supportedReasoningEfforts || []; }
  get fastSupported() { return !!this.selectedModel?.serviceTiers?.some(t => t.id === 'priority'); }
  status(id: string) { const p = this.probes.find(v => v.id === id); return !p ? '等待探测' : !p.ssh ? 'SSH 不可达' : p.codex ? (p.codex_version || 'Codex 可用') : '未检测到 Codex'; }
  hostName(id: string) { return this.hosts.find(h => h.id === id)?.name || id; }
  formatTime(v: number) { return new Date(v * 1000).toLocaleString(); }
  notify(v: string, failed = false) { this.message.set(v); this.failed.set(failed); }
  loadSessions() { this.api.get<{sessions: CodexSession[]}>('/api/codex/sessions').subscribe({ next: v => this.sessions.set(v.sessions), error: e => this.notify(e.message, true) }); }
  loadRemote() {
    if (!this.hostId) return;
    this.api.get<{threads: CodexRemoteThread[]; projects: CodexProject[]; models: CodexModel[]}>(`/api/codex/remote/catalog?host_id=${encodeURIComponent(this.hostId)}`).subscribe({
      next: v => { this.threads.set(v.threads); this.projects.set(v.projects); this.models.set(v.models);
        if (!this.modelId) { this.modelId = v.models.find(m => m.isDefault)?.id || v.models[0]?.id || ''; this.modelChanged(); } },
      error: e => this.notify(e.message, true),
    });
  }
  refreshAll() { this.loadSessions(); this.loadRemote(); }
  hostChanged() { this.selected.set(null); this.remoteSelected.set(null); this.modelId = ''; this.workspace = this.hosts.find(h => h.id === this.hostId)?.codex_workspace || ''; this.refreshAll(); }
  newSession() { this.selected.set(null); this.remoteSelected.set(null); this.view = 'sessions'; this.workspace = this.hosts.find(h => h.id === this.hostId)?.codex_workspace || ''; }
  plainChat() { this.workspace = ''; this.sandbox = 'read-only'; this.view = 'sessions'; this.createConfigured({title: `${this.hostName(this.hostId)} 普通聊天`, workspace: ''}); }
  modelChanged() { const m = this.selectedModel; this.effort = m?.defaultReasoningEffort || m?.supportedReasoningEfforts?.[0]?.reasoningEffort || ''; if (!this.fastSupported) this.fast = false; }
  createConfigured(extra: Partial<CodexSession> = {}) {
    const host = this.hosts.find(h => h.id === this.hostId); if (!host) return;
    this.api.post<{session: CodexSession}>('/api/codex/session/create', {
      host_id: host.id, title: extra.title || `${host.name} 对话`, workspace: extra.workspace ?? this.workspace,
      sandbox: this.sandbox, model: extra.model || this.modelId, reasoning_effort: extra.reasoning_effort || this.effort,
      service_tier: this.fast ? 'priority' : '', remote_thread_id: extra.remote_thread_id || '',
    }).subscribe({ next: v => { this.selected.set(v.session); this.remoteSelected.set(null); this.loadSessions(); this.notify('对话已准备好'); }, error: e => this.notify(e.message, true) });
  }
  open(s: CodexSession) { this.remoteSelected.set(null); this.api.get<{session: CodexSession}>(`/api/codex/session?id=${encodeURIComponent(s.id)}`).subscribe({ next: v => this.selected.set(v.session), error: e => this.notify(e.message, true) }); }
  openRemote(t: CodexRemoteThread) {
    this.selected.set(null); this.remoteSelected.set(t); this.remoteMessages.set([]);
    this.api.get<{thread: CodexRemoteThread}>(`/api/codex/remote/thread?host_id=${encodeURIComponent(this.hostId)}&thread_id=${encodeURIComponent(t.id)}`).subscribe({
      next: v => { const thread = v.thread; this.remoteSelected.set(thread); this.remoteMessages.set(this.extractMessages(thread)); },
      error: e => this.notify(e.message, true),
    });
  }
  extractMessages(t: CodexRemoteThread) {
    const out: Array<{role: string; content: string}> = [];
    for (const turn of t.turns || []) for (const item of turn.items || []) {
      const type = String(item['type'] || '').toLowerCase();
      const role = type.includes('user') ? 'user' : type.includes('agent') ? 'assistant' : '';
      let content = String(item['text'] || '');
      const value = item['content'];
      if (!content && Array.isArray(value)) content = value.map(v => typeof v === 'object' && v ? String((v as Record<string, unknown>)['text'] || '') : '').filter(Boolean).join('\n');
      if (role && content) out.push({role, content});
    }
    return out;
  }
  continueRemote(t: CodexRemoteThread) { this.workspace = t.cwd || ''; this.modelId = t.model || this.modelId; this.modelChanged(); this.effort = t.reasoningEffort || this.effort; this.createConfigured({title: t.name || '服务器历史会话', workspace: t.cwd || '', model: t.model || '', reasoning_effort: t.reasoningEffort || '', remote_thread_id: t.id}); }
  chooseProject(p: CodexProject) { this.workspace = p.path; this.newSession(); }
  refreshSelected() { const v = this.selected(); if (v) this.open(v); }
  send() { const v = this.selected(), p = this.prompt.trim(); if (!v || !p || v.status === 'running') return; this.prompt = ''; this.api.post<{session: CodexSession}>('/api/codex/message', {id: v.id, prompt: p}).subscribe({next: r => {this.selected.set(r.session); this.loadSessions();}, error: e => {this.prompt = p; this.notify(e.message, true);}}); }
  cancel() { const v = this.selected(); if (v) this.api.post('/api/codex/cancel', {id: v.id}).subscribe({next: () => setTimeout(() => this.refreshSelected(), 250), error: e => this.notify(e.message, true)}); }
  remove() { const v = this.selected(); if (!v || !confirm(`删除“${v.title}”及面板记录吗？服务器原始历史不会删除。`)) return; this.api.post('/api/codex/session/delete', {id: v.id}).subscribe({next: () => {this.selected.set(null); this.loadSessions();}, error: e => this.notify(e.message, true)}); }
  ngOnDestroy() { if (this.poll) clearInterval(this.poll); }
}
