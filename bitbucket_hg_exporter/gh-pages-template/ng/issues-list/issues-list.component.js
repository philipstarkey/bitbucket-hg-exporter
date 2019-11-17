'use strict';

// Register `issueList` component, along with its associated controller and template
angular.
  module('issuesList').
  component('issuesList', {
    templateUrl: 'ng/issues-list/issues-list.template.html',
    controller: ['$http', '$routeParams', '$rootScope', function IssueListController($http, $routeParams, $rootScope) {
        var self = this;
        self.orderProp = 'id';
        self.reverseSort = false;
        self.tableCols = [
            {title:"Title", index:"id"},
            {title:"Reporter", index:"reporter.display_name"},
            {title:"Type", index:"kind"},
            {title:"Priority", index:"priority"},
            {title:"Status", index:"status"},
            {title:"Votes", index:"votes"},
            {title:"Assignee", index:"assignee.display_name"},
            {title:"Component", index:"component.name"},
            {title:"Milestone", index:"milestone.name"},
            {title:"Version", index:"version.name"},
            {title:"Created", index:"created_on"},
            {title:"Updated", index:"updated_on"}
        ];

        //pagination info
        self.currentPage = $routeParams.pageId;
        self.project_slug = $routeParams.owner + '/' + $routeParams.project;
      
        $http.get($rootScope.projects[self.project_slug]['project_path']+'issues_page='+self.currentPage+'.json').then(function(response) {
            self.issues = response.data;
        });
        
    }]
  });