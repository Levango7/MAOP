describe('可视化工作流编辑器E2E测试', () => {
  beforeEach(() => {
    cy.visit('/visual-builder');
  });

  it('页面加载正常', () => {
    cy.get('input[placeholder="工作流名称"]').should('have.value', '新建工作流');
    cy.get('button').should('have.length', 5);
  });

  it('添加节点成功', () => {
    cy.get('button').contains('添加触发节点').click();
    cy.get('.react-flow__node').should('have.length', 1);

    cy.get('button').contains('添加LLM节点').click();
    cy.get('.react-flow__node').should('have.length', 2);
  });

  it('连接节点并导出YAML成功', () => {
    // 添加两个节点
    cy.get('button').contains('添加触发节点').click();
    cy.get('button').contains('添加响应节点').click();

    // 连接节点
    cy.get('.react-flow__node').first().find('.react-flow__handle-right').click();
    cy.get('.react-flow__node').last().find('.react-flow__handle-left').click();

    cy.get('.react-flow__edge').should('have.length', 1);

    // 导出YAML
    cy.get('button').contains('导出YAML').click();
    // 等待下载完成，实际场景中可验证下载文件内容
    cy.wait(1000);
  });

  it('修改工作流名称成功', () => {
    cy.get('input[placeholder="工作流名称"]').clear().type('告警自动处理工作流');
    cy.get('input[placeholder="工作流名称"]').should('have.value', '告警自动处理工作流');
  });
});
