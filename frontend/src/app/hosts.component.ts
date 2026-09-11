import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import { Credential, Host } from './models';

@Component({
  selector: 'app-hosts',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  template: `
    <header class="page-head">
      <div>
        <p class="eyebrow">SSH INVENTORY</p>
        <h1>主机与凭据</h1>
        <p>所有连接都锁定 SSH 主机指纹；跳板链最长 5 层。</p>
      </div>
      <button mat-flat-button (click)="newHost()">添加主机</button>
    </header>
    @if (message()) {
      <div class="alert" [class.error]="failed()">{{ message() }}</div>
    }
    <div class="split-layout">
      <section class="panel-section">
        <div class="section-title">
          <h2>主机清单</h2>
          <span>{{ hosts.length }} 台</span>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>地址</th>
                <th>路径</th>
                <th>终端</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (host of hosts; track host.id) {
                <tr>
                  <td class="long-value">{{ host.name }}</td>
                  <td class="mono long-value">{{ host.address }}:{{ host.port }}</td>
                  <td class="long-value">{{ route(host) }}</td>
                  <td>{{ host.terminal_enabled ? '启用' : '关闭' }}</td>
                  <td><button mat-button (click)="edit(host)">编辑</button></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>
      <mat-card class="editor-card">
        <h2>{{ draft.id ? '编辑主机' : '添加主机' }}</h2>
        <div class="form-grid">
          <mat-form-field appearance="outline"
            ><mat-label>显示名称</mat-label><input matInput [(ngModel)]="draft.name"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>分组</mat-label><input matInput [(ngModel)]="draft.group"
          /></mat-form-field>
          <mat-form-field appearance="outline" class="wide"
            ><mat-label>地址或域名</mat-label><input matInput [(ngModel)]="draft.address"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>SSH 端口</mat-label><input matInput type="number" [(ngModel)]="draft.port"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>SSH 账号</mat-label><input matInput [(ngModel)]="draft.username"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>凭据</mat-label
            ><mat-select [(ngModel)]="draft.credential_id">
              @for (item of credentials; track item.id) {
                <mat-option [value]="item.id">{{ item.name }}</mat-option>
              }
            </mat-select></mat-form-field
          >
          <mat-form-field appearance="outline"
            ><mat-label>跳板机</mat-label
            ><mat-select [(ngModel)]="draft.jump_id"
              ><mat-option value="">不使用跳板</mat-option>
              @for (item of availableJumps; track item.id) {
                <mat-option [value]="item.id">{{ item.name }}</mat-option>
              }
            </mat-select></mat-form-field
          >
          <mat-form-field appearance="outline" class="wide"
            ><mat-label>SSH 主机公钥</mat-label
            ><textarea matInput rows="4" [(ngModel)]="draft.host_key"></textarea
            ><mat-hint>先保存基本信息，再读取并人工核对指纹。</mat-hint></mat-form-field
          >
          <mat-form-field appearance="outline" class="wide"
            ><mat-label>Codex 默认工作目录（可选）</mat-label
            ><input matInput [(ngModel)]="draft.codex_workspace"
            /><mat-hint>留空时使用远端 SSH 账号的主目录。</mat-hint></mat-form-field
          >
        </div>
        <div class="check-row">
          <mat-checkbox [(ngModel)]="draft.favorite">收藏</mat-checkbox
          ><mat-checkbox [(ngModel)]="draft.terminal_enabled">允许网页终端</mat-checkbox
          ><mat-checkbox [(ngModel)]="draft.codex_enabled">允许 Codex 对话</mat-checkbox
          ><mat-checkbox [(ngModel)]="draft.browser_enabled">允许浏览器标签</mat-checkbox>
        </div>
        <div class="button-row">
          <button mat-flat-button (click)="save()">保存</button
          ><button mat-stroked-button [disabled]="!draft.id" (click)="inspect()">读取指纹</button
          ><button mat-stroked-button [disabled]="!draft.id" (click)="test()">测试连接</button
          ><button mat-button class="danger" [disabled]="!draft.id" (click)="remove()">删除</button>
        </div>
      </mat-card>
    </div>
    <section class="panel-section">
      <div class="section-title">
        <div>
          <h2>加密凭据库</h2>
          <p>私钥与密码使用本机 master key 加密后保存。</p>
        </div>
      </div>
      <div class="credential-row">
        <mat-form-field appearance="outline"
          ><mat-label>凭据名称</mat-label
          ><input matInput [(ngModel)]="credential.name" /></mat-form-field
        ><mat-form-field appearance="outline"
          ><mat-label>类型</mat-label
          ><mat-select [(ngModel)]="credential.kind"
            ><mat-option value="key">SSH 私钥</mat-option
            ><mat-option value="password">密码</mat-option></mat-select
          ></mat-form-field
        ><mat-form-field appearance="outline" class="secret-field"
          ><mat-label>{{ credential.kind === 'key' ? '私钥内容' : '密码' }}</mat-label
          ><textarea matInput rows="3" [(ngModel)]="credential.secret"></textarea></mat-form-field
        ><button mat-flat-button (click)="saveCredential()">保存凭据</button>
      </div>
      <div class="chip-list">
        @for (item of credentials; track item.id) {
          <span class="soft-chip"
            >{{ item.name }} · {{ item.kind }}
            <button type="button" aria-label="删除凭据" (click)="deleteCredential(item)">
              ×
            </button></span
          >
        }
      </div>
    </section>
  `,
})
export class HostsComponent {
  @Input({ required: true }) hosts: Host[] = [];
  @Input({ required: true }) credentials: Credential[] = [];
  @Output() changed = new EventEmitter<void>();
  readonly message = signal('');
  readonly failed = signal(false);
  draft = this.emptyHost();
  credential = { name: '', kind: 'key' as 'key' | 'password', secret: '', passphrase: '' };
  constructor(private readonly api: ApiService) {}
  get availableJumps() {
    return this.hosts.filter((value) => value.id !== this.draft.id);
  }
  emptyHost(): Host {
    return {
      id: '',
      name: '',
      address: '',
      port: 22,
      username: '',
      credential_id: '',
      jump_id: '',
      host_key: '',
      group: '',
      favorite: false,
      terminal_enabled: true,
      codex_enabled: true,
      browser_enabled: true,
      codex_workspace: '',
    };
  }
  newHost() {
    this.draft = this.emptyHost();
  }
  edit(host: Host) {
    this.draft = { ...host };
  }
  route(host: Host) {
    const jump = this.hosts.find((value) => value.id === host.jump_id);
    return jump ? `${jump.name} → ${host.name}` : '直连';
  }
  notify(message: string, failed = false) {
    this.message.set(message);
    this.failed.set(failed);
  }
  save() {
    this.api.post<{ host: Host }>('/api/host/save', this.draft).subscribe({
      next: (value) => {
        this.draft = { ...value.host };
        this.notify('主机已保存');
        this.changed.emit();
      },
      error: (e) => this.notify(e.message, true),
    });
  }
  inspect() {
    this.api
      .post<{ host_key: string; fingerprint: string }>('/api/host/inspect', { id: this.draft.id })
      .subscribe({
        next: (value) => {
          this.draft.host_key = value.host_key;
          this.notify(`请核对指纹：${value.fingerprint}`);
        },
        error: (e) => this.notify(e.message, true),
      });
  }
  test() {
    this.api
      .post<{ ok: boolean; output: string; route: string[] }>('/api/host/test', {
        id: this.draft.id,
      })
      .subscribe({
        next: (value) =>
          this.notify(
            `${value.ok ? '连接成功' : '连接失败'}：${value.route.join(' → ')}\n${value.output}`,
            !value.ok,
          ),
        error: (e) => this.notify(e.message, true),
      });
  }
  remove() {
    if (!confirm(`确定删除“${this.draft.name}”吗？`)) return;
    this.api.post('/api/host/delete', { id: this.draft.id }).subscribe({
      next: () => {
        this.newHost();
        this.notify('主机已删除');
        this.changed.emit();
      },
      error: (e) => this.notify(e.message, true),
    });
  }
  saveCredential() {
    this.api.post('/api/credential/save', this.credential).subscribe({
      next: () => {
        this.credential = { name: '', kind: 'key', secret: '', passphrase: '' };
        this.notify('凭据已加密保存');
        this.changed.emit();
      },
      error: (e) => this.notify(e.message, true),
    });
  }
  deleteCredential(item: Credential) {
    if (!confirm(`确定删除凭据“${item.name}”吗？`)) return;
    this.api
      .post('/api/credential/delete', { id: item.id })
      .subscribe({ next: () => this.changed.emit(), error: (e) => this.notify(e.message, true) });
  }
}
