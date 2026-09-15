import { Component, Input, OnChanges, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import { ClashStatus, Host, ProbeResult } from './models';

@Component({
  selector: 'app-clash',
  imports: [FormsModule, MatButtonModule, MatCardModule, MatFormFieldModule,
    MatInputModule, MatProgressBarModule, MatSelectModule],
  template: `
    <header class="page-head"><div><p class="eyebrow">CLASH / MIHOMO</p>
      <h1>代理与 TUN</h1><p>控制目标机器本地的 Clash 模式、系统代理、TUN 和首条规则；配置修改前自动在远端生成备份。</p></div>
      <button mat-stroked-button (click)="load()" [disabled]="!hostId || busy()">刷新状态</button></header>
    @if (busy()) { <mat-progress-bar mode="indeterminate" /> }
    @if (message()) { <div class="alert" [class.error]="failed()">{{ message() }}</div> }
    <mat-card class="editor-card clash-card">
      <mat-form-field appearance="outline" class="wide"><mat-label>目标机器</mat-label>
        <mat-select [(ngModel)]="hostId" (ngModelChange)="load()">
          @for (host of hosts; track host.id) {
            <mat-option [value]="host.id">{{ host.name }} · {{ probe(host.id)?.os || host.address }}</mat-option>
          }
        </mat-select>
      </mat-form-field>
      @if (status()) {
        <div class="metric-grid clash-metrics">
          <mat-card><span>核心</span><strong>{{ status()!.running ? '运行中' : status()!.installed ? '未连接' : '未安装' }}</strong></mat-card>
          <mat-card><span>模式</span><strong>{{ modeLabel(status()!.mode) }}</strong></mat-card>
          <mat-card><span>系统代理</span><strong>{{ status()!.system_proxy?.enabled === true ? '已开启' : status()!.system_proxy?.enabled === false ? '已关闭' : '不可读取' }}</strong></mat-card>
          <mat-card><span>TUN</span><strong>{{ status()!.tun ? '已开启' : '已关闭' }}</strong></mat-card>
        </div>
        <dl class="status-list">
          <div><dt>版本</dt><dd>{{ status()!.version || '未知' }}</dd></div>
          <div><dt>混合端口</dt><dd>{{ status()!.mixed_port || '未配置' }}</dd></div>
          <div><dt>配置文件</dt><dd class="mono long-value">{{ status()!.config_path || '未找到' }}</dd></div>
          <div><dt>Controller</dt><dd>{{ status()!.controller ? '本机接口已启用' : '未启用' }}</dd></div>
        </dl>
        @if (status()!.error) { <div class="alert error long-value">{{ status()!.error }}</div> }
        <section class="panel-section"><div class="section-title"><div><h2>流量模式</h2>
          <p>规则模式按规则表选择；全局模式把流量交给当前全局节点；直连模式绕过代理。</p></div></div>
          <div class="button-row">
            <button mat-flat-button (click)="setMode('rule')" [disabled]="!status()!.running">规则模式</button>
            <button mat-stroked-button (click)="setMode('global')" [disabled]="!status()!.running">全局模式</button>
            <button mat-stroked-button (click)="setMode('direct')" [disabled]="!status()!.running">直连模式</button>
          </div>
        </section>
        <section class="panel-section"><div class="section-title"><div><h2>策略组与节点</h2>
          <p>选择一个策略组，再指定该组当前使用的节点。选择结果会立即写入正在运行的 Clash 核心。</p></div></div>
          @if (status()!.groups?.length) {
            <div class="form-grid">
              <mat-form-field appearance="outline"><mat-label>策略组</mat-label>
                <mat-select [(ngModel)]="groupName" (ngModelChange)="groupChanged()">
                  @for (group of status()!.groups; track group.name) {
                    <mat-option [value]="group.name">{{ group.name }} · 当前 {{ group.now }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline"><mat-label>节点</mat-label>
                <mat-select [(ngModel)]="proxyName">
                  @for (name of selectedGroup?.all || []; track name) { <mat-option [value]="name">{{ name }}</mat-option> }
                </mat-select>
              </mat-form-field>
            </div>
            <div class="button-row"><button mat-flat-button (click)="selectProxy()" [disabled]="!groupName || !proxyName">应用节点</button></div>
          } @else { <p class="muted">当前核心没有返回可选择的策略组。</p> }
        </section>
        <section class="panel-section"><div class="section-title"><div><h2>接管方式</h2>
          <p>系统代理影响遵循操作系统代理设置的应用；TUN 在网络层接管更多程序，切换时可能短暂中断远程连接。</p></div></div>
          <div class="button-row">
            <button mat-flat-button (click)="toggleProxy(true)" [disabled]="status()!.system_proxy?.supported !== true">开启系统代理</button>
            <button mat-stroked-button (click)="toggleProxy(false)" [disabled]="status()!.system_proxy?.supported !== true">关闭系统代理</button>
            <button mat-flat-button (click)="toggleTun(true)" [disabled]="!status()!.controller">开启 TUN</button>
            <button mat-stroked-button (click)="toggleTun(false)" [disabled]="!status()!.controller">关闭 TUN</button>
          </div>
        </section>
        <section class="panel-section"><div class="section-title"><div><h2>添加最高优先级规则</h2>
          <p>示例：DOMAIN-SUFFIX,openai.com,🚀 节点选择。规则会插到 rules 顶部，并热加载配置。</p></div></div>
          <mat-form-field appearance="outline" class="wide"><mat-label>规则</mat-label>
            <input matInput [(ngModel)]="rule" placeholder="DOMAIN-SUFFIX,openai.com,策略组" />
          </mat-form-field>
          <div class="button-row"><button mat-flat-button (click)="addRule()" [disabled]="!rule.trim() || !status()!.controller">验证、备份并添加</button></div>
        </section>
        <details><summary>当前规则与策略组</summary>
          <p class="long-value"><strong>策略组：</strong>{{ status()!.policies?.join('、') || '未读取' }}</p>
          <pre class="rule-list">{{ ruleText }}</pre>
        </details>
      } @else { <div class="empty-state">选择机器后读取 Clash/Mihomo 状态。</div> }
    </mat-card>
  `,
})
export class ClashComponent implements OnChanges {
  @Input() hosts: Host[] = [];
  @Input() probes: ProbeResult[] = [];
  hostId = '';
  rule = '';
  groupName = '';
  proxyName = '';
  readonly status = signal<ClashStatus | null>(null);
  readonly busy = signal(false);
  readonly message = signal('');
  readonly failed = signal(false);
  constructor(private readonly api: ApiService) {}
  ngOnChanges() { if (!this.hostId && this.hosts.length) { this.hostId = this.hosts[0].id; this.load(); } }
  probe(id: string) { return this.probes.find((item) => item.id === id); }
  modeLabel(value: string) { return ({ rule: '规则', global: '全局', direct: '直连' } as Record<string, string>)[value] || value; }
  get ruleText() { return (this.status()?.rules || []).map((item) => `${item.type || ''},${item.payload || ''},${item.proxy || ''}`).join('\n'); }
  notify(value: string, failed = false) { this.message.set(value); this.failed.set(failed); }
  load() {
    if (!this.hostId) return;
    this.busy.set(true);
    this.api.get<ClashStatus>('/api/clash?host_id=' + encodeURIComponent(this.hostId)).subscribe({
      next: (value) => { this.setStatus(value); this.busy.set(false); this.notify('状态已刷新'); },
      error: (error) => { this.busy.set(false); this.notify(error.message, true); },
    });
  }
  reauthThen(action: () => void) {
    const password = prompt('此操作会修改远端代理配置，请输入管理员密码：');
    if (password === null) return;
    this.api.post('/api/reauth', { password }).subscribe({ next: action, error: (e) => this.notify(e.message, true) });
  }
  act(payload: Record<string, unknown>, success: string) {
    this.reauthThen(() => {
      this.busy.set(true);
      this.api.post<ClashStatus>('/api/clash/action', { host_id: this.hostId, ...payload }).subscribe({
        next: (value) => { this.setStatus(value); this.busy.set(false); this.notify(success + (value.backup ? `；备份：${value.backup}` : '')); },
        error: (e) => { this.busy.set(false); this.notify(e.message, true); },
      });
    });
  }
  setMode(mode: 'rule' | 'global' | 'direct') { this.act({ action: 'mode', mode }, '模式已切换'); }
  toggleProxy(enabled: boolean) { this.act({ action: 'system_proxy', enabled }, enabled ? '系统代理已开启' : '系统代理已关闭'); }
  toggleTun(enabled: boolean) {
    if (!confirm(`${enabled ? '开启' : '关闭'} TUN 可能短暂影响目标机器网络，继续吗？`)) return;
    this.act({ action: 'tun', enabled }, enabled ? 'TUN 已开启' : 'TUN 已关闭');
  }
  addRule() { this.act({ action: 'rule_add', rule: this.rule.trim() }, '规则已添加并重新加载'); }
  get selectedGroup() { return (this.status()?.groups || []).find((item) => item.name === this.groupName); }
  setStatus(value: ClashStatus) {
    this.status.set(value);
    const groups = value.groups || [];
    if (!groups.some((item) => item.name === this.groupName)) this.groupName = groups[0]?.name || '';
    this.groupChanged(false);
  }
  groupChanged(reset = true) {
    const group = this.selectedGroup;
    if (reset || !group?.all.includes(this.proxyName)) this.proxyName = group?.now || group?.all[0] || '';
  }
  selectProxy() { this.act({ action: 'proxy_select', group: this.groupName, name: this.proxyName }, '节点已切换'); }
}
