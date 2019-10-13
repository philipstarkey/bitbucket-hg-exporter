'use strict';

// Register `issueDetails` component, along with its associated controller and template
angular.
  module('issueDetails').
  component('issueDetails', {
    templateUrl: 'ng/issue-details/issue-details.template.html',
    controller: ['$http', '$routeParams', '$sce', '$rootScope', function IssueDetailsController($http, $routeParams, $sce, $rootScope) {
        var self = this;

        //pagination info
        self.issueId = $routeParams.issueId;
        self.commentPage = $routeParams.pageId;
        self.mainHtml = "";
      
        $http.get($rootScope.relative_project_url+'issues/'+self.issueId+'.json').then(function(response) {
            self.issue = response.data;
            self.mainHtml = $sce.trustAsHtml(self.issue['content']['html']);
        });

        $http.get($rootScope.relative_project_url+'issues/'+self.issueId+'/commentspagelen=100&page='+self.commentPage+'.json').then(function(response) {
          self.comments = response.data;
          angular.forEach(self.comments['values'], function(value, index){
            self.comments['values'][index]['content']['html'] = $sce.trustAsHtml(self.comments['values'][index]['content']['html']);
          });
      });
        
    }]
  });