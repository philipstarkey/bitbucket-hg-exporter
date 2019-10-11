'use strict';

// Register `issueList` component, along with its associated controller and template
angular.
  module('issuesList').
  component('issuesList', {
    templateUrl: 'issues-list/issues-list.template.html',
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
      
        $http.get($rootScope.relative_project_url+'issuespagelen=100&page='+self.currentPage+'.json').then(function(response) {
            self.issues = response.data;
        });
        
    }]
  });